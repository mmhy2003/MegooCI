"""The assistant endpoints with tools: tool mode, the fallback, the stream.

The AI library is replaced by a scripted fake, so no network is needed.
"""
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; stub it so the module imports.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

import app.api.v1.ai_assistant as ai
import app.api.v1.system as system_api
from tests._rbac import build_inmemory_factory, make_role, make_user

YAML = (
    "version: 1\n"
    "name: demo\n"
    "stages:\n"
    "  - name: build\n"
    "    steps:\n"
    "      - run: make build\n"
)
NEW_YAML = YAML.replace("make build", "make all")


# ── a scripted stand-in for the AI library ──────────────────────────────

class FakeProviderError(Exception):
    def __init__(self, message="provider said no"):
        super().__init__(message)
        self.message = message


class AuthenticationError(FakeProviderError): ...
class APIConnectionError(FakeProviderError): ...
class Timeout(FakeProviderError): ...
class RateLimitError(FakeProviderError): ...
class BadRequestError(FakeProviderError): ...


def tool_response(*calls):
    """A model answer asking for tools. calls: (id, name, arguments dict)."""
    tool_calls = [
        SimpleNamespace(id=i, function=SimpleNamespace(name=n, arguments=json.dumps(a)))
        for i, n, a in calls
    ]
    message = SimpleNamespace(content=None, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=SimpleNamespace(total_tokens=10))


def text_response(text):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=SimpleNamespace(total_tokens=5))


class Provider:
    """Answers each model call with the next scripted item; an exception is raised."""

    def __init__(self):
        self.script = []
        self.calls = []

    def will(self, *items):
        self.script.extend(items)

    async def acompletion(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return await item()
        return item


@pytest.fixture
def provider(monkeypatch):
    fake = Provider()
    monkeypatch.setattr(ai.litellm, "acompletion", fake.acompletion)
    monkeypatch.setattr(ai.litellm, "exceptions", SimpleNamespace(
        AuthenticationError=AuthenticationError,
        APIConnectionError=APIConnectionError,
        Timeout=Timeout,
        RateLimitError=RateLimitError,
        BadRequestError=BadRequestError,
    ))
    return fake


@pytest.fixture(autouse=True)
def ai_enabled(monkeypatch):
    async def no_overrides(db):
        return {}

    monkeypatch.setattr(system_api, "get_ai_overrides", no_overrides)
    monkeypatch.setattr(system_api, "resolve_ai_config", lambda overrides=None: {
        "enabled": True, "provider": "custom", "model": "local-model",
        "reasoning_model": None, "api_key": "k", "base_url": "http://llm.test/v1",
    })


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine, factory = await build_inmemory_factory()
    monkeypatch.setattr(ai.database, "async_session", factory)
    yield factory
    await engine.dispose()


def admin():
    return make_user(is_admin=True)


async def ask(sf, prompt="change it", *, current_yaml=YAML, user=None, **fields):
    body = ai.AssistantRequest(prompt=prompt, current_yaml=current_yaml, **fields)
    async with sf() as db:
        return await ai.pipeline_assistant(body, db, user or admin())


async def stream(sf, prompt="change it", *, current_yaml=YAML, user=None):
    """Run the streaming endpoint and return its parsed events and raw chunks."""
    body = ai.AssistantRequest(prompt=prompt, current_yaml=current_yaml)
    async with sf() as db:
        response = await ai.pipeline_assistant_stream(body, db, user or admin())
    chunks = [chunk async for chunk in response.body_iterator]
    events = [json.loads(c[len("data: "):]) for c in chunks if c.startswith("data: ")]
    return response, events, chunks


# ── tool mode ───────────────────────────────────────────────────────────

async def test_tool_mode_edits_and_returns_a_proposal(sf, provider):
    provider.will(
        tool_response(("c1", "replace_text", {"old": "make build", "new": "make all"}),
                      ("c2", "validate", {})),
        text_response("I switched the build command to `make all`."),
    )

    response = await ask(sf)

    assert response.mode == "tools" and response.limit_reached is False
    assert response.reply == "I switched the build command to `make all`."
    assert [(s.tool, s.label, s.ok) for s in response.steps] == [
        ("replace_text", "Replaced text at line 6", True),
        ("validate", "Validated · no problems", True),
    ]
    proposal = response.proposal
    assert proposal.yaml == NEW_YAML == response.yaml
    assert (proposal.added, proposal.removed, proposal.problems) == (1, 1, [])
    changed = [(l.kind, l.old, l.new, l.text) for l in proposal.hunks[0].lines if l.kind != "context"]
    assert changed == [("remove", 6, None, "      - run: make build"),
                       ("add", None, 6, "      - run: make all")]


async def test_tool_mode_sends_the_tools_the_short_prompt_and_the_numbered_yaml(sf, provider):
    provider.will(text_response("Nothing to do."))

    await ask(sf, "what does this do?")

    call = provider.calls[0]
    assert call["model"] == "openai/local-model"
    assert call["tool_choice"] == "auto"
    assert [t["function"]["name"] for t in call["tools"]][:2] == ["read_lines", "search"]
    assert call["api_base"] == "http://llm.test/v1"
    system, user = call["messages"][0], call["messages"][-1]
    assert system["content"].startswith(ai.TOOL_SYSTEM_PROMPT)
    assert "6 |       - run: make build" in user["content"]
    assert user["content"].endswith("My request: what does this do?")


async def test_the_tool_results_go_back_to_the_model_after_its_own_message(sf, provider):
    first = tool_response(("c1", "read_lines", {"start": 2, "end": 2}))
    provider.will(first, text_response("ok"))

    await ask(sf)

    second_call = provider.calls[1]["messages"]
    assert second_call[-2] is first.choices[0].message, "the provider's own message object is kept"
    assert second_call[-1] == {"role": "tool", "tool_call_id": "c1", "content": "2 | name: demo"}


async def test_a_question_gets_an_answer_and_no_proposal(sf, provider):
    provider.will(text_response("It builds the project with make."))

    response = await ask(sf, "what does this do?")

    assert response.reply == "It builds the project with make."
    assert response.proposal is None and response.yaml is None and response.steps == []


async def test_a_snippet_in_an_answer_is_not_a_proposal(sf, provider):
    answer = "Add it like this:\n```yaml\n- name: lint\n  run: make lint\n```"
    provider.will(text_response(answer))

    response = await ask(sf, "how do I add a lint step?")

    assert response.proposal is None
    assert response.reply == answer


async def test_a_model_that_ignores_the_tools_and_writes_the_pipeline_still_gets_a_proposal(sf, provider):
    provider.will(text_response(f"```yaml\n{NEW_YAML}```\nSwitched to make all."))

    response = await ask(sf)

    assert response.mode == "tools"
    assert response.proposal.yaml == NEW_YAML
    assert response.reply == f"```yaml\n{NEW_YAML}```\nSwitched to make all.", "the reply is left whole"


async def test_yaml_in_the_reply_is_ignored_once_the_tools_made_the_change(sf, provider):
    provider.will(
        tool_response(("c1", "replace_text", {"old": "make build", "new": "make all"})),
        text_response("Done:\n```yaml\nname: other\nstages: []\n```"),
    )

    response = await ask(sf)

    assert response.proposal.yaml == NEW_YAML


async def test_an_invalid_result_is_proposed_with_its_problems(sf, provider):
    provider.will(
        tool_response(("c1", "replace_text", {"old": "      - run: make build",
                                               "new": "      - kube_apply:\n          manifests: [k8s/]"})),
        text_response("Added a deploy step."),
    )

    response = await ask(sf)

    assert len(response.proposal.problems) == 1
    problem = response.proposal.problems[0]
    assert problem.line == 6 and "kubeconfig" in problem.message


async def test_edits_that_cancel_out_give_no_proposal(sf, provider):
    provider.will(
        tool_response(("c1", "replace_text", {"old": "make build", "new": "make all"}),
                      ("c2", "replace_text", {"old": "make all", "new": "make build"})),
        text_response("On reflection nothing needs to change."),
    )

    response = await ask(sf)

    assert response.proposal is None and len(response.steps) == 2


async def test_a_new_pipeline_in_an_empty_editor(sf, provider):
    provider.will(
        tool_response(("c1", "write_document", {"text": YAML})),
        text_response("Created a starter pipeline."),
    )

    response = await ask(sf, "create a pipeline", current_yaml=None)

    assert "My editor is empty" in provider.calls[0]["messages"][-1]["content"]
    assert (response.proposal.added, response.proposal.removed) == (6, 0)


async def test_the_tool_call_limit_is_reported(sf, provider):
    provider.will(*[tool_response((f"c{i}", "read_lines", {})) for i in range(17)])

    response = await ask(sf)

    assert response.limit_reached is True
    assert len(response.steps) == 16 and len(provider.calls) == 17
    assert response.reply.startswith("I ran out of steps before making a change")


async def test_the_limit_with_changes_made_keeps_them_as_a_proposal(sf, provider):
    provider.will(
        tool_response(("c0", "replace_text", {"old": "make build", "new": "make all"})),
        *[tool_response((f"c{i}", "read_lines", {})) for i in range(1, 17)],
    )

    response = await ask(sf)

    assert response.limit_reached is True and response.proposal.yaml == NEW_YAML
    assert "check them before applying" in response.reply


# ── fallback to a call without tools ────────────────────────────────────

async def test_a_provider_that_rejects_tools_falls_back_to_one_call_without_them(sf, provider):
    provider.will(
        BadRequestError("this model does not support tools"),
        text_response(f"```yaml\n{NEW_YAML}```\nSwitched to make all."),
    )

    response = await ask(sf)

    assert response.mode == "legacy" and response.steps == []
    assert response.proposal.yaml == NEW_YAML == response.yaml
    assert response.reply == "Switched to make all."
    retry = provider.calls[1]
    assert "tools" not in retry and "tool_choice" not in retry
    assert retry["messages"][0]["content"].startswith(ai.SYSTEM_PROMPT)
    assert "COMPLETE updated pipeline YAML" in retry["messages"][1]["content"]
    assert retry["messages"][-1] == {"role": "user", "content": "change it"}


async def test_any_other_failure_of_the_first_call_also_falls_back(sf, provider):
    provider.will(RuntimeError("unexpected"), text_response("Just an answer."))

    response = await ask(sf)

    assert response.mode == "legacy" and response.reply == "Just an answer."
    assert response.proposal is None


async def test_fallback_reply_that_is_only_yaml_gets_a_default_text(sf, provider):
    provider.will(BadRequestError(), text_response(NEW_YAML))

    response = await ask(sf)

    assert response.proposal.yaml == NEW_YAML
    assert response.reply == "I updated the pipeline. Review the changes below."


@pytest.mark.parametrize("error, detail", [
    (AuthenticationError("bad key"), "AI provider authentication failed: bad key"),
    (APIConnectionError("no route"), "AI provider unreachable: no route"),
    (Timeout("too slow"), "AI provider error: too slow"),
    (RateLimitError("slow down"), "AI provider error: slow down"),
])
async def test_failures_a_retry_cannot_fix_are_reported_without_a_second_call(sf, provider, error, detail):
    provider.will(error)

    with pytest.raises(HTTPException) as exc:
        await ask(sf)

    assert exc.value.status_code == 502 and exc.value.detail == detail
    assert len(provider.calls) == 1


async def test_when_the_fallback_fails_too_its_error_is_reported(sf, provider):
    provider.will(RuntimeError("tools?"), BadRequestError("context too long"))

    with pytest.raises(HTTPException) as exc:
        await ask(sf)

    assert exc.value.status_code == 502
    assert exc.value.detail == "AI provider rejected request: context too long"


async def test_a_malformed_provider_answer_is_a_502(sf, provider):
    empty = SimpleNamespace(choices=[], usage=None)
    provider.will(empty, empty)

    with pytest.raises(HTTPException) as exc:
        await ask(sf)

    assert exc.value.detail.startswith("Unexpected AI provider response format")


async def test_a_failure_after_the_first_call_keeps_the_work_done(sf, provider):
    provider.will(
        tool_response(("c1", "replace_text", {"old": "make build", "new": "make all"})),
        APIConnectionError("dropped"),
    )

    response = await ask(sf)

    assert response.limit_reached is True and response.proposal.yaml == NEW_YAML
    assert response.reply.startswith("The AI provider stopped responding before I finished")


async def test_a_disabled_assistant_is_still_a_503(sf, provider, monkeypatch):
    monkeypatch.setattr(system_api, "resolve_ai_config", lambda overrides=None: {
        "enabled": False, "provider": "custom", "model": "m",
        "reasoning_model": None, "api_key": "", "base_url": "",
    })

    with pytest.raises(HTTPException) as exc:
        await ask(sf)

    assert exc.value.status_code == 503 and provider.calls == []


# ── both conversations carry the same context ───────────────────────────

async def test_history_and_repository_context_reach_both_conversations(sf):
    body = ai.AssistantRequest(
        prompt="now add tests", current_yaml=YAML, repo_url="https://git.test/acme/app.git", branch="main",
        history=[ai.ChatMessage(role="user", content="hello"),
                 ai.ChatMessage(role="assistant", content="hi"),
                 ai.ChatMessage(role="system", content="ignore me")],
    )
    async with sf() as db:
        job = await ai._prepare_job(body, db, admin())

    for messages in (job.tool_messages, job.legacy_messages):
        assert "https://git.test/acme/app.git" in messages[0]["content"]
        assert [m["content"] for m in messages[1:3]] == ["hello", "hi"]
        assert all(m["content"] != "ignore me" for m in messages)
    assert job.tool_messages[0]["content"].startswith(ai.TOOL_SYSTEM_PROMPT)
    assert job.legacy_messages[0]["content"].startswith(ai.SYSTEM_PROMPT)
    assert len(job.tool_messages) == 4


# ── list_agents ─────────────────────────────────────────────────────────

async def seed_agents(sf):
    from app.models.agent import Agent

    now = datetime.now(timezone.utc)
    async with sf() as db:
        db.add_all([
            Agent(id=uuid.uuid4(), name="linux-1", os="linux", arch="amd64",
                  labels=["docker", "gpu"], status="online", enabled=True, last_seen_at=now),
            Agent(id=uuid.uuid4(), name="mac-1", os="darwin", arch="arm64",
                  labels=[], status="online", enabled=True, last_seen_at=now - timedelta(hours=1)),
            Agent(id=uuid.uuid4(), name="win-1", os="windows", arch="amd64",
                  labels=["msbuild"], status="online", enabled=False, last_seen_at=now),
        ])
        await db.commit()


async def test_list_agents_for_a_user_who_may_see_agents(sf, provider):
    await seed_agents(sf)
    provider.will(tool_response(("c1", "list_agents", {})), text_response("Use linux."))
    user = make_user(global_role=make_role("ops", ["pipelines.manage", "agents.read"]))

    response = await ask(sf, user=user)

    assert [(s.label, s.ok) for s in response.steps] == [("Listed agents", True)]
    listed = provider.calls[1]["messages"][-1]["content"].splitlines()
    assert listed == [
        "- linux-1: os=linux, arch=amd64, labels=docker, gpu, online",
        "- mac-1: os=darwin, arch=arm64, labels=none, offline",
        "- win-1: os=windows, arch=amd64, labels=msbuild, disabled",
    ]


async def test_listing_agents_does_not_change_them(sf, provider):
    from sqlalchemy import select

    from app.models.agent import Agent

    await seed_agents(sf)
    provider.will(tool_response(("c1", "list_agents", {})), text_response("ok"))

    await ask(sf)

    async with sf() as db:
        stored = (await db.execute(select(Agent.status).where(Agent.name == "mac-1"))).scalar_one()
    assert stored == "online", "the stale agent is reported offline but its row is left alone"


async def test_list_agents_without_the_permission(sf, provider):
    await seed_agents(sf)
    provider.will(tool_response(("c1", "list_agents", {})), text_response("I cannot see agents."))
    user = make_user(global_role=make_role("dev", ["pipelines.manage"]))

    response = await ask(sf, user=user)

    assert [(s.label, s.ok) for s in response.steps] == [("Listed agents · not permitted", False)]
    told = provider.calls[1]["messages"][-1]["content"]
    assert "does not have permission" in told and "linux-1" not in told


async def test_list_agents_when_there_are_none(sf, provider):
    provider.will(tool_response(("c1", "list_agents", {})), text_response("ok"))

    await ask(sf)

    assert provider.calls[1]["messages"][-1]["content"] == "No agents are registered."


# ── the stream ──────────────────────────────────────────────────────────

async def test_stream_sends_a_step_per_tool_call_then_done(sf, provider):
    provider.will(
        tool_response(("c1", "search", {"pattern": "make"})),
        tool_response(("c2", "replace_text", {"old": "make build", "new": "make all"})),
        text_response("Switched to make all."),
    )

    response, events, _ = await stream(sf)

    assert response.media_type == "text/event-stream"
    assert response.headers["x-accel-buffering"] == "no"
    assert [e["type"] for e in events] == ["step", "step", "done"]
    assert events[0] == {"type": "step", "tool": "search", "label": "Searched “make” · 1 match", "ok": True}
    done = events[-1]
    assert done["reply"] == "Switched to make all." and done["mode"] == "tools"
    assert done["proposal"]["yaml"] == NEW_YAML and done["yaml"] == NEW_YAML
    assert [s["label"] for s in done["steps"]] == [events[0]["label"], events[1]["label"]]


async def test_stream_done_matches_the_plain_endpoint(sf, provider):
    script = [
        tool_response(("c1", "replace_text", {"old": "make build", "new": "make all"})),
        text_response("Done."),
    ]
    provider.will(*script)
    plain = await ask(sf)
    provider.will(*script)

    _, events, _ = await stream(sf)

    assert events[-1] == {"type": "done", **plain.model_dump()}


async def test_stream_reports_a_provider_failure_as_one_error_event(sf, provider):
    provider.will(AuthenticationError("bad key"))

    _, events, _ = await stream(sf)

    assert events == [{"type": "error", "detail": "AI provider authentication failed: bad key"}]


async def test_stream_fallback_still_ends_with_done(sf, provider):
    provider.will(BadRequestError("no tools"), text_response(f"```yaml\n{NEW_YAML}```"))

    _, events, _ = await stream(sf)

    assert [e["type"] for e in events] == ["done"]
    assert events[0]["mode"] == "legacy" and events[0]["proposal"]["yaml"] == NEW_YAML


async def test_stream_sends_keep_alive_comments_while_the_model_is_silent(sf, provider, monkeypatch):
    monkeypatch.setattr(ai, "KEEPALIVE_SECONDS", 0.02)

    async def slow():
        await asyncio.sleep(0.15)
        return text_response("Late answer.")

    provider.will(slow)

    _, events, chunks = await stream(sf)

    assert chunks.count(": keep-alive\n\n") >= 2
    assert chunks[-1].startswith("data: ") and events[-1]["reply"] == "Late answer."


async def test_closing_the_stream_cancels_the_model_call(sf, provider):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def hangs():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    provider.will(hangs)
    body = ai.AssistantRequest(prompt="change it", current_yaml=YAML)
    async with sf() as db:
        job = await ai._prepare_job(body, db, admin())
    events = ai._stream_events(job)
    pending = asyncio.ensure_future(events.__anext__())
    await asyncio.wait_for(started.wait(), 2)

    pending.cancel()  # the client went away
    with pytest.raises(asyncio.CancelledError):
        await pending
    await asyncio.wait_for(cancelled.wait(), 2)


# ── review fixes ────────────────────────────────────────────────────────

EXAMPLE = (
    "version: 1\n"
    "name: build-app\n"
    "runs_on: linux\n"
    "stages:\n"
    "  - name: build\n"
    "    steps:\n"
    "      - run: make\n"
)


async def test_a_question_answered_with_a_full_example_is_not_a_proposal(sf, provider):
    """The example is another pipeline; proposing it would offer to replace the user's."""
    answer = f"Put it at the top, like this:\n```yaml\n{EXAMPLE}```\nIt cannot go inside a stage."
    provider.will(tool_response(("c1", "reference", {"topic": "runs_on"})), text_response(answer))

    response = await ask(sf, "how do I use runs_on? show an example")

    assert response.proposal is None and response.yaml is None
    assert response.reply == answer, "the example stays in the answer"


async def test_a_full_pipeline_in_a_reply_is_proposed_when_the_editor_is_empty(sf, provider):
    provider.will(text_response(f"```yaml\n{EXAMPLE}```"))

    response = await ask(sf, "create a pipeline", current_yaml=None)

    assert response.proposal.yaml == EXAMPLE and response.proposal.added == 7


async def test_a_later_failure_with_nothing_changed_falls_back_like_a_first_one(sf, provider):
    """For example an endpoint that accepts tools but rejects tool results."""
    provider.will(
        tool_response(("c1", "read_lines", {})),
        BadRequestError("messages with role 'tool' are not supported"),
        text_response(f"```yaml\n{NEW_YAML}```\nSwitched to make all."),
    )

    response = await ask(sf)

    assert response.mode == "legacy" and response.limit_reached is False
    assert response.proposal.yaml == NEW_YAML and response.reply == "Switched to make all."
    assert [s.label for s in response.steps] == ["Read lines 1–6"]
    assert len(provider.calls) == 3 and "tools" not in provider.calls[2]
    assert provider.calls[2]["messages"][0]["content"].startswith(ai.SYSTEM_PROMPT)


@pytest.mark.parametrize("error, detail", [
    (AuthenticationError("key revoked"), "AI provider authentication failed: key revoked"),
    (RateLimitError("slow down"), "AI provider error: slow down"),
])
async def test_a_later_failure_a_retry_cannot_fix_is_reported_when_nothing_changed(sf, provider, error, detail):
    provider.will(tool_response(("c1", "read_lines", {})), error)

    with pytest.raises(HTTPException) as exc:
        await ask(sf)

    assert exc.value.status_code == 502 and exc.value.detail == detail
    assert len(provider.calls) == 2


async def test_ollama_models_get_the_tools_through_the_chat_api(sf, provider, monkeypatch):
    """LiteLLM's "ollama/" provider cannot pass tools on: it switches the model
    to JSON-only output instead. "ollama_chat/" passes them, and a model
    without tool support is rejected, which falls back."""
    monkeypatch.setattr(system_api, "resolve_ai_config", lambda overrides=None: {
        "enabled": True, "provider": "ollama", "model": "llama3.1",
        "reasoning_model": None, "api_key": "", "base_url": "http://ollama.test:11434",
    })
    provider.will(
        BadRequestError("registry.ollama.ai/library/llama3.1 does not support tools"),
        text_response("Just an answer."),
    )

    response = await ask(sf)

    assert provider.calls[0]["model"] == "ollama_chat/llama3.1" and "tools" in provider.calls[0]
    assert provider.calls[1]["model"] == "ollama/llama3.1" and "tools" not in provider.calls[1]
    assert response.mode == "legacy" and response.reply == "Just an answer."


async def test_other_providers_keep_their_model_id_for_tool_calls(sf, provider):
    provider.will(text_response("ok"))
    await ask(sf)
    assert provider.calls[0]["model"] == "openai/local-model"


async def test_editor_content_over_the_size_limit_is_refused_before_any_model_call(sf, provider):
    too_big = "a: b\n" * (256 * 1024 // 5 + 1)

    with pytest.raises(HTTPException) as exc:
        await ask(sf, current_yaml=too_big)

    assert exc.value.status_code == 413 and "256 KiB" in exc.value.detail
    assert provider.calls == []


async def test_editor_content_at_the_size_limit_is_accepted(sf, provider):
    provider.will(text_response("ok"))
    response = await ask(sf, current_yaml="a" * (256 * 1024 - 1) + "\n")
    assert response.reply == "ok"


async def test_the_database_connection_is_given_back_before_the_model_is_called(sf, provider):
    """A request waits on the model for minutes; holding a pooled connection
    all that time would starve the rest of the application."""
    from tests._rbac import seed_project

    async with sf() as db:
        project_id = await seed_project(db, "P")
        await db.commit()
    in_transaction = []
    body = ai.AssistantRequest(prompt="change it", current_yaml=YAML, project_id=str(project_id))

    async with sf() as db:
        async def model_call():
            in_transaction.append(db.in_transaction())
            return text_response("ok")

        provider.will(model_call)
        await ai.pipeline_assistant(body, db, admin())

    async with sf() as db:
        provider.will(model_call)
        response = await ai.pipeline_assistant_stream(body, db, admin())
        in_transaction.append(db.in_transaction())
        assert [chunk async for chunk in response.body_iterator]

    assert in_transaction == [False, False, False]
