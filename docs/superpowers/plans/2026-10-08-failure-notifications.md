# Build Failure Notifications Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a pipeline name, in its YAML, the notification channels that receive a message when one of its builds fails, and teach the in-app docs and the AI assistant the new YAML.

**Architecture:** The validator gains rules for a top-level `notifications` block; the compiler ignores it. A new server module reads the block from the pipeline's stored YAML, builds a default or custom message, and sends it through the existing channel code. The executor calls that module once, at the point where a build's final status has been committed as failed. Nothing runs on the agent.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy 2 async, PyYAML, pytest with `asyncio_mode=auto` and in-memory SQLite (`tests/_rbac.py`), Next.js + TypeScript for the docs panel.

**Spec:** `docs/superpowers/specs/2026-10-08-failure-notifications-design.md`

## Global Constraints

- **Branch:** all work happens on `feat/failure-notifications`. Never commit to `main`.
- **Commits:** conventional style (`feat(pipeline): …`, `feat(builds): …`, `docs(pipeline): …`). End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **YAML shape:** top-level key `notifications`, whose only allowed key is `on_failure`: a non-empty list. Each entry is a non-empty channel-name string, or a mapping with only `channel` (required), `message`, `subject`, `recipient`, each a non-empty string.
- **Only failed builds notify.** Successful and cancelled builds send nothing.
- **Server-side only.** No change to the agent, the database schema, or the compiled build graph.
- **Best-effort.** Nothing in this feature may change a build's status or raise into the executor.
- **Entries are independent.** A problem with one entry never stops the others.
- **Never put a provider's error text in the build log.** It can quote a channel's webhook URL or bot token, and the build log is readable by everyone who can see the build. The error stays on the delivery row.
- **The block is read from the pipeline's YAML as stored when the build ends,** not as it was at trigger time.
- **New placeholders, available only in notification messages:** `${{ build.failed_stage }}`, `${{ build.failed_step }}`, `${{ build.url }}` (`{MEGOOCI_PUBLIC_URL}/builds/{build id}`). `${{ build.status }}` is `failed`.
- **Telegram:** for a Telegram channel, the default message and the values substituted for placeholders from the `build`, `pipeline`, `project` and `megooci` namespaces are HTML-escaped. Text the author wrote is not.
- **Reuse:** send through `app.services.notification_service.send_notification`; render with `app.services.step_actions.interpolation.interpolate_value`.
- **Backend tests:** run from `backend/` with `./.venv/Scripts/python.exe -m pytest`.
- **Frontend gate:** `npx tsc --noEmit` from `frontend/`. There is no frontend unit-test harness, and `npm run lint` is broken; do not use it.
- **Line endings:** the files being modified use CRLF. Keep them: use an editor or the Edit tool, not a script that rewrites the whole file with LF.
- **Paths in commands:** `git` commands use paths relative to the repository root. Commands are written for Git Bash.

## Review Focus

Inputs the spec implies but does not spell out. Each has a test in the task that owns the code.

1. **A stage, step, branch or pipeline name containing `<` or `&`, sent to a Telegram channel.** Telegram reads the message as HTML and rejects a stray `<`. Expected: the message is delivered with those characters escaped. Pinned in Task 2 (`test_telegram_values_are_html_escaped_but_authored_tags_are_kept`).
2. **A delivery that fails with an error quoting the webhook URL or bot token.** Expected: the build log says the delivery failed without the secret. Pinned in Task 2 (`test_failed_delivery_is_reported_without_the_provider_error`).
3. **A misspelled or disabled channel name.** Expected: the author is told in the build log, and the other entries are still sent. Pinned in Task 2 (`test_each_entry_is_sent_independently`) and Task 3 (`test_unknown_channel_is_reported_in_the_build_log`).
4. **The pipeline's YAML is missing, broken or no longer has the block when the build ends.** Expected: nothing is sent and nothing crashes. Pinned in Task 2 (`test_reader_returns_nothing_for_missing_or_malformed_input`, `test_pipeline_with_no_yaml_sends_nothing`).
5. **The notification code itself throws.** Expected: the build still ends as failed and the executor finishes its work. Pinned in Task 3 (`test_error_while_sending_never_changes_the_build_result`).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/services/pipeline_compiler.py` | Modify | `_notification_errors` and its call; docstring. |
| `backend/app/services/build_notifications.py` | Create | Read the block, build the message, send it. |
| `backend/app/services/build_executor.py` | Modify | `_notify_build_failure` and one call at the end of a failed build. |
| `backend/app/api/v1/ai_assistant.py` | Modify | Prompt section, structure example, notify note, rule 12. |
| `frontend/src/components/pipeline/docs-panel.tsx` | Modify | "Failure Notifications" section and two text updates. |
| `README.md` | Modify | Example. |
| `backend/tests/_failure_notifications.py` | Create | Seeding helpers and a fake Redis. |
| `backend/tests/test_failure_notifications_yaml.py` | Create | Validator tests. |
| `backend/tests/test_build_notifications.py` | Create | Reader, message and sending tests. |
| `backend/tests/test_failure_notifications_executor.py` | Create | Executor tests. |
| `backend/tests/test_failure_notifications_docs.py` | Create | The prompt's examples pass the validator. |

---

### Task 1: Validate the `notifications` block

**Files:**
- Modify: `backend/app/services/pipeline_compiler.py` (module docstring; a new helper above `_structure_errors`; one call inside `_structure_errors`)
- Test: `backend/tests/test_failure_notifications_yaml.py` (new)

**Interfaces:**
- Produces: module constants `NOTIFICATION_EVENTS = ("on_failure",)` and `NOTIFICATION_ENTRY_FIELDS = ("channel", "message", "subject", "recipient")`.
- Produces: `_notification_errors(value: Any, line_map: dict[int, int], top_line: int) -> list[PipelineError]`.
- Produces: `validate_pipeline(yaml)` and `validate_pipeline_definition(yaml)` report mistakes in the block. Entry messages start with `notifications.on_failure[<index>]: `.

**How the validator works (no change needed):** `_structure_errors(data, line_map)` receives the parsed YAML and a table mapping `id(mapping)` to the 1-based line where that mapping starts. For a block mapping that is the line of its first key, so the `notifications` mapping's line is the line of `on_failure:`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_failure_notifications_yaml.py`:

```python
"""Validation of the top-level `notifications` block in pipeline YAML."""

import pytest

from app.services.pipeline_compiler import (
    compile_to_build_graph,
    normalize_runs_on,
    parse_yaml_pipeline,
    validate_pipeline,
    validate_pipeline_definition,
)

STAGES = (
    "stages:\n"
    "  - name: deploy\n"
    "    steps:\n"
    "      - run: ./deploy.sh\n"
)


def _pipeline(notifications_block: str) -> str:
    return "name: demo\n" + notifications_block + STAGES


SHORT = _pipeline(
    "notifications:\n"
    "  on_failure:\n"
    "    - deploy-alerts\n"
)

FULL = _pipeline(
    "notifications:\n"
    "  on_failure:\n"
    "    - deploy-alerts\n"
    "    - channel: ops-email\n"
    "      recipient: oncall@example.com\n"
    "      subject: \"Staging deploy failed\"\n"
    "      message: |\n"
    "        Build #${{ build.number }} of ${{ pipeline.name }} failed\n"
    "        at ${{ build.failed_stage }} / ${{ build.failed_step }}\n"
    "        ${{ build.url }}\n"
)


def test_short_form_passes():
    assert validate_pipeline(SHORT) == []


def test_full_form_and_both_together_pass():
    assert validate_pipeline(FULL) == []


def test_same_channel_may_appear_twice():
    block = (
        "notifications:\n"
        "  on_failure:\n"
        "    - channel: ops-email\n"
        "      recipient: a@example.com\n"
        "    - channel: ops-email\n"
        "      recipient: b@example.com\n"
    )
    assert validate_pipeline(_pipeline(block)) == []


def test_pipeline_without_the_block_is_unaffected():
    assert validate_pipeline(_pipeline("")) == []


def test_block_does_not_change_what_the_pipeline_compiles_to():
    with_block = compile_to_build_graph(parse_yaml_pipeline(FULL))
    without = compile_to_build_graph(parse_yaml_pipeline(_pipeline("")))
    assert with_block == without
    assert normalize_runs_on(parse_yaml_pipeline(FULL).get("runs_on")) is None


@pytest.mark.parametrize(
    "block, expected",
    [
        ("notifications: deploy-alerts\n", "'notifications' must be a mapping"),
        ("notifications: [deploy-alerts]\n", "'notifications' must be a mapping"),
        ("notifications: {}\n", "'notifications' requires 'on_failure'"),
        (
            "notifications:\n  on_fail:\n    - deploy-alerts\n",
            "'notifications' has unknown key(s): on_fail (allowed: on_failure)",
        ),
        (
            "notifications:\n  on_failure: deploy-alerts\n",
            "'notifications.on_failure' must be a non-empty list",
        ),
        (
            "notifications:\n  on_failure: []\n",
            "'notifications.on_failure' must be a non-empty list",
        ),
        (
            "notifications:\n  on_failure:\n",
            "'notifications.on_failure' must be a non-empty list",
        ),
        (
            "notifications:\n  on_failure:\n    - 42\n",
            "notifications.on_failure[0]: must be a channel name or a mapping",
        ),
        (
            "notifications:\n  on_failure:\n    - \"  \"\n",
            "notifications.on_failure[0]: channel name must not be empty",
        ),
        (
            "notifications:\n  on_failure:\n    - recipient: a@example.com\n",
            "notifications.on_failure[0]: requires 'channel'",
        ),
        (
            "notifications:\n  on_failure:\n    - channel: \"\"\n",
            "notifications.on_failure[0]: requires 'channel'",
        ),
        (
            "notifications:\n  on_failure:\n    - chanel: deploy-alerts\n",
            "notifications.on_failure[0]: unknown field(s): chanel "
            "(allowed: channel, message, subject, recipient)",
        ),
        (
            "notifications:\n  on_failure:\n    - channel: a\n      message: \"\"\n",
            "notifications.on_failure[0]: 'message' must be a non-empty string",
        ),
        (
            "notifications:\n  on_failure:\n    - channel: a\n      subject: 5\n",
            "notifications.on_failure[0]: 'subject' must be a non-empty string",
        ),
        (
            "notifications:\n  on_failure:\n    - channel: a\n      recipient: [x]\n",
            "notifications.on_failure[0]: 'recipient' must be a non-empty string",
        ),
    ],
)
def test_block_rules(block, expected):
    errors = validate_pipeline(_pipeline(block))
    assert any(expected in e for e in errors), errors


def test_second_entry_is_named_by_its_index():
    block = (
        "notifications:\n"
        "  on_failure:\n"
        "    - deploy-alerts\n"
        "    - recipient: a@example.com\n"
    )
    errors = validate_pipeline(_pipeline(block))
    assert any("notifications.on_failure[1]: requires 'channel'" in e for e in errors), errors


def test_entry_error_carries_the_entry_line():
    block = (
        "notifications:\n"        # line 2
        "  on_failure:\n"         # line 3
        "    - deploy-alerts\n"   # line 4
        "    - chanel: ops\n"     # line 5
    )
    errors = validate_pipeline_definition(_pipeline(block))
    match = [e for e in errors if "unknown field(s): chanel" in e.message]
    assert match, errors
    assert match[0].line == 5


def test_block_error_carries_the_block_line():
    block = (
        "notifications:\n"        # line 2
        "  on_fail:\n"            # line 3
        "    - deploy-alerts\n"
    )
    errors = validate_pipeline_definition(_pipeline(block))
    match = [e for e in errors if "unknown key(s): on_fail" in e.message]
    assert match, errors
    assert match[0].line == 3


def test_notification_errors_are_reported_together_with_stage_errors():
    yaml_doc = (
        "name: demo\n"
        "notifications:\n"
        "  on_fail: []\n"
        "stages:\n"
        "  - name: deploy\n"
        "    steps:\n"
        "      - kube_apply:\n"
        "          manifests: [k8s/]\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("unknown key(s): on_fail" in e for e in errors), errors
    assert any("requires 'kubeconfig'" in e for e in errors), errors
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_failure_notifications_yaml.py -q`
Expected: `19 failed, 5 passed`. The five that pass are the valid-pipeline tests: today the validator ignores the block entirely, so valid input already passes and every rule test fails.

- [ ] **Step 3: Add the validation helper**

In `backend/app/services/pipeline_compiler.py`, insert this block immediately above `def _structure_errors(`:

```python
NOTIFICATION_EVENTS = ("on_failure",)
NOTIFICATION_ENTRY_FIELDS = ("channel", "message", "subject", "recipient")


def _notification_errors(
    value: Any, line_map: dict[int, int], top_line: int
) -> list[PipelineError]:
    """Validate the top-level ``notifications`` block (who is told when a
    build fails). ``build_notifications`` reads and sends it."""
    if not isinstance(value, dict):
        return [PipelineError(message="'notifications' must be a mapping", line=top_line)]

    block_line = line_map.get(id(value)) or top_line
    errors: list[PipelineError] = []

    unknown = sorted(str(k) for k in value if k not in NOTIFICATION_EVENTS)
    if unknown:
        errors.append(
            PipelineError(
                message=(
                    f"'notifications' has unknown key(s): {', '.join(unknown)} "
                    f"(allowed: {', '.join(NOTIFICATION_EVENTS)})"
                ),
                line=block_line,
            )
        )

    if "on_failure" not in value:
        if not unknown:
            errors.append(
                PipelineError(message="'notifications' requires 'on_failure'", line=block_line)
            )
        return errors

    entries = value["on_failure"]
    if not isinstance(entries, list) or not entries:
        errors.append(
            PipelineError(
                message=(
                    "'notifications.on_failure' must be a non-empty list of "
                    "channel names or mappings"
                ),
                line=block_line,
            )
        )
        return errors

    for index, entry in enumerate(entries):
        prefix = f"notifications.on_failure[{index}]"

        if isinstance(entry, str):
            if not entry.strip():
                errors.append(
                    PipelineError(
                        message=f"{prefix}: channel name must not be empty", line=block_line
                    )
                )
            continue

        if not isinstance(entry, dict):
            errors.append(
                PipelineError(
                    message=f"{prefix}: must be a channel name or a mapping", line=block_line
                )
            )
            continue

        entry_line = line_map.get(id(entry)) or block_line
        unknown_fields = sorted(str(k) for k in entry if k not in NOTIFICATION_ENTRY_FIELDS)
        if unknown_fields:
            errors.append(
                PipelineError(
                    message=(
                        f"{prefix}: unknown field(s): {', '.join(unknown_fields)} "
                        f"(allowed: {', '.join(NOTIFICATION_ENTRY_FIELDS)})"
                    ),
                    line=entry_line,
                )
            )

        channel = entry.get("channel")
        if not isinstance(channel, str) or not channel.strip():
            errors.append(
                PipelineError(message=f"{prefix}: requires 'channel'", line=entry_line)
            )

        for field in ("message", "subject", "recipient"):
            if field in entry and (
                not isinstance(entry[field], str) or not entry[field].strip()
            ):
                errors.append(
                    PipelineError(
                        message=f"{prefix}: '{field}' must be a non-empty string",
                        line=entry_line,
                    )
                )

    return errors
```

- [ ] **Step 4: Call it from `_structure_errors`**

In `_structure_errors`, insert the call immediately above the line `stages = data.get("stages")`:

```python
    if "notifications" in data:
        errors.extend(_notification_errors(data["notifications"], line_map, top_line))

    stages = data.get("stages")
```

- [ ] **Step 5: Mention the block in the module docstring**

Add one line to the "Supports:" list at the top of the file, after the artifacts line:

```python
- artifacts collection (stage-level glob paths)
- notifications (top-level: who is told when a build fails)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_failure_notifications_yaml.py -q`
Expected: `24 passed`.

Run the whole suite: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `383 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/pipeline_compiler.py backend/tests/test_failure_notifications_yaml.py
git commit -m "feat(pipeline): validate the notifications block"
```

---

### Task 2: Read the block and send the messages

**Files:**
- Create: `backend/app/services/build_notifications.py`
- Create: `backend/tests/_failure_notifications.py`
- Test: `backend/tests/test_build_notifications.py` (new)

**Interfaces:**
- Consumes: `send_notification(db, channel_id, message, *, subject=None, recipient=None, build_id=None, step_id=None) -> NotificationDelivery` from `app.services.notification_service`. It commits, records a delivery row, and returns it with `status` `"sent"` or `"failed"` (the provider's error in `error`). It raises `ValueError` for a missing or disabled channel.
- Consumes: `interpolate_value(value, secrets, env, builtins)` from `app.services.step_actions.interpolation`, where `builtins` is `{"build": {...}, "pipeline": {...}, "project": {...}, "megooci": {...}}` with string values.
- Produces (`app.services.build_notifications`):
  - `FailureNotification(channel: str, message: str | None = None, subject: str | None = None, recipient: str | None = None)`, a frozen dataclass.
  - `failure_notifications(yaml_content: str | None) -> list[FailureNotification]`. Never raises.
  - `failed_step_of(build) -> tuple[str, str, Step | None]`: stage name, step name and the step of the first failed step, or `("", "", None)`. Needs `build.stages` and their `steps` loaded.
  - `build_url(build_id) -> str`, `default_subject(values) -> str`, `default_message(values) -> str`.
  - `async send_failure_notifications(db, build, *, secrets, env_vars, builtins, report=None) -> int`. `report` is an optional `async def report(text: str) -> None` called once per entry that could not be delivered, with a line that is safe to show in the build log. Returns the number of messages sent.
- Produces (`tests._failure_notifications`): `PIPELINE_YAML`, `FakeRedis`, `seed_channel(db, name, channel_type="slack", *, enabled=True, config=None) -> uuid`, `seed_build(db, **overrides) -> dict` with keys `project`, `pipeline`, `build`, `stage`, `step`, `load_build(db, build_id)`, `builtins_for(build)`.

**Facts about the existing senders (in `notification_service.py`):**
- Three private functions do the network calls: `_send_slack(config, message, recipient)` and `_send_telegram(config, message, recipient)` (async), and `_send_email_sync(config, to_email, subject, body)` (sync, run in a thread). The tests replace these three, so the real `send_notification` and its delivery rows are exercised.
- Telegram is sent with `parse_mode: "HTML"`.
- An email entry with no `recipient` is sent to the channel's `from_email`.

- [ ] **Step 1: Write the test helpers**

Create `backend/tests/_failure_notifications.py`:

```python
"""Seeding helpers for failure-notification tests."""
import uuid

PIPELINE_YAML = (
    "name: deploy-staging\n"
    "notifications:\n"
    "  on_failure:\n"
    "    - deploy-alerts\n"
    "stages:\n"
    "  - name: deploy\n"
    "    steps:\n"
    "      - run: ./deploy.sh\n"
)


class FakeRedis:
    """Just enough of redis.asyncio for the executor."""

    def __init__(self):
        self.store = {}
        self.published = []

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, *args, **kwargs):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def publish(self, channel, message):
        self.published.append((channel, message))
        return 0

    async def aclose(self):
        pass


async def seed_channel(db, name, channel_type="slack", *, enabled=True, config=None):
    """Insert a notification channel and return its id."""
    from app.models.notification import NotificationChannel
    from app.models.user import User
    from app.services.notification_service import encrypt_channel_config

    defaults = {
        "slack": {"webhook_url": "https://hooks.slack.example/services/T0/B0/hook-secret"},
        "telegram": {"bot_token": "123456:bot-token-secret", "default_chat_id": "-100"},
        "email": {"smtp_host": "smtp.example.com", "smtp_port": 587,
                  "from_email": "ci@example.com"},
    }
    owner = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="admin")
    db.add(owner)
    await db.flush()
    channel = NotificationChannel(
        id=uuid.uuid4(),
        name=name,
        channel_type=channel_type,
        config_encrypted=encrypt_channel_config(config or defaults[channel_type]),
        enabled=enabled,
        created_by=owner.id,
    )
    db.add(channel)
    await db.flush()
    return channel.id


async def seed_build(
    db,
    *,
    yaml_content=PIPELINE_YAML,
    pipeline_name="deploy-staging",
    project_name="Inbox Staging",
    build_status="failed",
    step_status="failed",
    stage_name="deploy",
    step_name="apply manifests",
    branch="develop",
    commit_sha="3f2a9c1d5e6f7a8b",
):
    """Insert project → pipeline → build → one stage → one step. Returns ids."""
    from app.models.build import Build, Stage, Step
    from app.models.pipeline import Pipeline
    from app.models.project import Project
    from app.models.user import User

    owner = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="owner")
    db.add(owner)
    await db.flush()
    project = Project(id=uuid.uuid4(), name=project_name,
                      slug=f"p-{uuid.uuid4().hex[:8]}", created_by=owner.id)
    db.add(project)
    await db.flush()
    pipeline = Pipeline(id=uuid.uuid4(), name=pipeline_name, project_id=project.id,
                        created_by=owner.id, yaml_content=yaml_content)
    db.add(pipeline)
    await db.flush()
    build = Build(id=uuid.uuid4(), pipeline_id=pipeline.id, number=428, status=build_status,
                  trigger_type="webhook", branch=branch, commit_sha=commit_sha)
    db.add(build)
    await db.flush()
    stage = Stage(id=uuid.uuid4(), build_id=build.id, name=stage_name,
                  status=step_status, sort_order=0)
    db.add(stage)
    await db.flush()
    step = Step(id=uuid.uuid4(), stage_id=stage.id, name=step_name, step_type="run",
                status=step_status, sort_order=0)
    db.add(step)
    await db.flush()
    return {"project": project.id, "pipeline": pipeline.id, "build": build.id,
            "stage": stage.id, "step": step.id}


async def load_build(db, build_id):
    """The build with stages and steps loaded, as the executor holds it."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.build import Build, Stage

    return (await db.execute(
        select(Build).where(Build.id == build_id)
        .options(selectinload(Build.stages).selectinload(Stage.steps))
    )).scalar_one()


def builtins_for(build, *, pipeline_name="deploy-staging", project_name="Inbox Staging"):
    """Placeholder values shaped like build_executor._load_scope_context's."""
    return {
        "build": {"id": str(build.id), "number": str(build.number),
                  "branch": build.branch or "", "commit": build.commit_sha or "",
                  "status": "running"},
        "pipeline": {"id": str(build.pipeline_id), "name": pipeline_name},
        "project": {"name": project_name},
        "megooci": {"url": "https://api.example.com"},
    }
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_build_notifications.py`:

```python
"""Reading the notifications block, building the message, and sending it."""
import os

import pytest
import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._failure_notifications import (
    PIPELINE_YAML,
    builtins_for,
    load_build,
    seed_build,
    seed_channel,
)
from tests._rbac import build_inmemory_factory


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture
def providers(monkeypatch):
    """Replace the three network senders; record what each was asked to send."""
    calls = {"slack": [], "telegram": [], "email": []}

    async def fake_slack(config, message, recipient):
        calls["slack"].append({"message": message, "recipient": recipient})

    async def fake_telegram(config, message, recipient):
        calls["telegram"].append({"message": message, "recipient": recipient})

    def fake_email(config, to_email, subject, body):
        calls["email"].append({"to": to_email, "subject": subject, "message": body})

    monkeypatch.setattr("app.services.notification_service._send_slack", fake_slack)
    monkeypatch.setattr("app.services.notification_service._send_telegram", fake_telegram)
    monkeypatch.setattr("app.services.notification_service._send_email_sync", fake_email)
    return calls


def _yaml(on_failure_block: str) -> str:
    return (
        "name: deploy-staging\n"
        "notifications:\n"
        "  on_failure:\n" + on_failure_block +
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )


async def _send(sf, build_id, **kwargs):
    from app.services.build_notifications import send_failure_notifications

    reports = []

    async def report(text):
        reports.append(text)

    async with sf() as db:
        build = await load_build(db, build_id)
        sent = await send_failure_notifications(
            db, build, secrets=kwargs.get("secrets", {}), env_vars=kwargs.get("env_vars", {}),
            builtins=builtins_for(build), report=report,
        )
    return sent, reports


# ── reading the block ───────────────────────────────────────────────────

def test_short_and_full_entries_normalize_to_the_same_shape():
    from app.services.build_notifications import FailureNotification, failure_notifications

    entries = failure_notifications(_yaml(
        "    - deploy-alerts\n"
        "    - channel: ops-email\n"
        "      recipient: oncall@example.com\n"
        "      subject: Deploy failed\n"
        "      message: It broke\n"
    ))
    assert entries == [
        FailureNotification(channel="deploy-alerts"),
        FailureNotification(channel="ops-email", message="It broke",
                            subject="Deploy failed", recipient="oncall@example.com"),
    ]


@pytest.mark.parametrize(
    "yaml_content",
    [
        None,
        "",
        "name: x\nstages: []\n",                             # no block
        "name: [unclosed\n",                                 # invalid YAML
        "- just\n- a list\n",                                # not a mapping
        "notifications: deploy-alerts\n",                    # block not a mapping
        "notifications:\n  on_failure: deploy-alerts\n",     # not a list
        "notifications:\n  on_success:\n    - a\n",          # other event only
    ],
)
def test_reader_returns_nothing_for_missing_or_malformed_input(yaml_content):
    from app.services.build_notifications import failure_notifications

    assert failure_notifications(yaml_content) == []


def test_reader_skips_malformed_entries_and_keeps_good_ones():
    from app.services.build_notifications import failure_notifications

    entries = failure_notifications(_yaml(
        "    - 42\n"
        "    - \"\"\n"
        "    - recipient: nobody\n"
        "    - good-channel\n"
    ))
    assert [e.channel for e in entries] == ["good-channel"]


# ── the message ─────────────────────────────────────────────────────────

VALUES = {
    "build": {"number": "428", "branch": "develop", "commit": "3f2a9c1d5e6f7a8b",
              "failed_stage": "deploy", "failed_step": "apply manifests",
              "url": "https://ci.example.com/builds/abc"},
    "pipeline": {"name": "deploy-staging-inbox"},
    "project": {"name": "Inbox Staging"},
}


def test_default_message_and_subject():
    from app.services.build_notifications import default_message, default_subject

    assert default_subject(VALUES) == "Build #428 of deploy-staging-inbox failed"
    assert default_message(VALUES) == (
        "Build #428 of deploy-staging-inbox failed\n"
        "Project: Inbox Staging | Branch: develop | Commit: 3f2a9c1\n"
        "Failed at: stage \"deploy\", step \"apply manifests\"\n"
        "https://ci.example.com/builds/abc"
    )


def test_default_message_leaves_out_what_the_build_does_not_have():
    from app.services.build_notifications import default_message

    values = {
        "build": {"number": "7", "branch": "", "commit": "", "failed_stage": "",
                  "failed_step": "", "url": "https://ci.example.com/builds/x"},
        "pipeline": {"name": "nightly"},
        "project": {},
    }
    assert default_message(values) == (
        "Build #7 of nightly failed\n"
        "https://ci.example.com/builds/x"
    )


# ── sending ─────────────────────────────────────────────────────────────

async def test_sends_the_default_message_and_records_a_delivery(sf, providers, monkeypatch):
    from app.models.notification import NotificationDelivery

    monkeypatch.setenv("MEGOOCI_PUBLIC_URL", "https://ci.example.com/")
    from app.config import get_settings
    get_settings.cache_clear()

    async with sf() as db:
        channel_id = await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db)
        await db.commit()

    sent, reports = await _send(sf, ids["build"])
    get_settings.cache_clear()

    assert sent == 1 and reports == []
    message = providers["slack"][0]["message"]
    assert message == (
        "Build #428 of deploy-staging failed\n"
        "Project: Inbox Staging | Branch: develop | Commit: 3f2a9c1\n"
        "Failed at: stage \"deploy\", step \"apply manifests\"\n"
        f"https://ci.example.com/builds/{ids['build']}"
    )
    async with sf() as db:
        rows = (await db.execute(select(NotificationDelivery))).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel_id == channel_id
    assert rows[0].build_id == ids["build"]
    assert rows[0].status == "sent"


async def test_custom_message_subject_and_recipient(sf, providers):
    yaml_content = _yaml(
        "    - channel: ops-email\n"
        "      recipient: oncall@example.com\n"
        "      subject: \"${{ pipeline.name }} is ${{ build.status }}\"\n"
        "      message: |\n"
        "        #${{ build.number }} failed at ${{ build.failed_stage }} / ${{ build.failed_step }}\n"
        "        token ${{ secrets.NOTE }} env ${{ env.REGION }}\n"
        "        ${{ build.url }}\n"
    )
    async with sf() as db:
        await seed_channel(db, "ops-email", "email")
        ids = await seed_build(db, yaml_content=yaml_content)
        await db.commit()

    sent, reports = await _send(sf, ids["build"], secrets={"NOTE": "s3"}, env_vars={"REGION": "eu"})

    assert sent == 1 and reports == []
    mail = providers["email"][0]
    assert mail["to"] == "oncall@example.com"
    assert mail["subject"] == "deploy-staging is failed"
    assert mail["message"].startswith("#428 failed at deploy / apply manifests\ntoken s3 env eu\n")
    assert mail["message"].rstrip().endswith(f"/builds/{ids['build']}")


async def test_each_entry_is_sent_independently(sf, providers):
    yaml_content = _yaml(
        "    - deploy-alerts\n"
        "    - missing-channel\n"
        "    - disabled-channel\n"
        "    - channel: tg-ops\n"
        "      recipient: \"-200\"\n"
    )
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        await seed_channel(db, "disabled-channel", enabled=False)
        await seed_channel(db, "tg-ops", "telegram")
        ids = await seed_build(db, yaml_content=yaml_content)
        await db.commit()

    sent, reports = await _send(sf, ids["build"])

    assert sent == 2
    assert len(providers["slack"]) == 1
    assert providers["telegram"][0]["recipient"] == "-200"
    assert reports == [
        "Failure notification not sent: channel 'missing-channel' was not found.",
        "Failure notification not sent: channel 'disabled-channel' is disabled.",
    ]


async def test_failed_delivery_is_reported_without_the_provider_error(sf, monkeypatch):
    """Provider errors quote the webhook URL or bot token; the build log is
    readable by everyone who can see the build."""
    from app.models.notification import NotificationDelivery

    async def failing_slack(config, message, recipient):
        raise RuntimeError(f"404 for url '{config['webhook_url']}'")

    monkeypatch.setattr("app.services.notification_service._send_slack", failing_slack)
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db)
        await db.commit()

    sent, reports = await _send(sf, ids["build"])

    assert sent == 0
    assert len(reports) == 1
    assert "channel 'deploy-alerts' could not be delivered" in reports[0]
    assert "hook-secret" not in reports[0] and "hooks.slack" not in reports[0]
    async with sf() as db:
        row = (await db.execute(select(NotificationDelivery))).scalar_one()
    assert row.status == "failed" and "hook-secret" in row.error


async def test_a_failing_entry_does_not_stop_the_next(sf, providers, monkeypatch):
    async def failing_slack(config, message, recipient):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.notification_service._send_slack", failing_slack)
    yaml_content = _yaml("    - deploy-alerts\n    - tg-ops\n")
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        await seed_channel(db, "tg-ops", "telegram")
        ids = await seed_build(db, yaml_content=yaml_content)
        await db.commit()

    sent, reports = await _send(sf, ids["build"])

    assert sent == 1 and len(reports) == 1
    assert len(providers["telegram"]) == 1


async def test_telegram_values_are_html_escaped_but_authored_tags_are_kept(sf, providers):
    """Telegram reads the message as HTML and rejects a stray < or &."""
    yaml_content = _yaml(
        "    - tg-default\n"
        "    - channel: tg-custom\n"
        "      message: \"<b>${{ pipeline.name }}</b> failed at ${{ build.failed_step }}\"\n"
    )
    async with sf() as db:
        await seed_channel(db, "tg-default", "telegram")
        await seed_channel(db, "tg-custom", "telegram")
        ids = await seed_build(db, yaml_content=yaml_content, step_name="build <web> & api",
                               branch="feat/a&b")
        await db.commit()

    sent, _ = await _send(sf, ids["build"])

    assert sent == 2
    default, custom = (c["message"] for c in providers["telegram"])
    assert "step \"build &lt;web&gt; &amp; api\"" in default
    assert "Branch: feat/a&amp;b" in default
    assert custom == "<b>deploy-staging</b> failed at build &lt;web&gt; &amp; api"


async def test_slack_and_email_values_are_not_escaped(sf, providers):
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db, step_name="build <web> & api")
        await db.commit()

    await _send(sf, ids["build"])

    assert "step \"build <web> & api\"" in providers["slack"][0]["message"]


async def test_email_entry_without_recipient_uses_default_subject(sf, providers):
    async with sf() as db:
        await seed_channel(db, "ops-email", "email")
        ids = await seed_build(db, yaml_content=_yaml("    - ops-email\n"))
        await db.commit()

    await _send(sf, ids["build"])

    assert providers["email"][0]["subject"] == "Build #428 of deploy-staging failed"


async def test_pipeline_without_the_block_sends_nothing(sf, providers):
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db, yaml_content="name: x\nstages:\n  - name: a\n    steps:\n      - run: x\n")
        await db.commit()

    sent, reports = await _send(sf, ids["build"])

    assert sent == 0 and reports == [] and providers["slack"] == []


async def test_pipeline_with_no_yaml_sends_nothing(sf, providers):
    async with sf() as db:
        ids = await seed_build(db, yaml_content=None)
        await db.commit()

    assert await _send(sf, ids["build"]) == (0, [])


async def test_build_with_no_failed_step_still_sends(sf, providers):
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db, step_status="success")
        await db.commit()

    sent, _ = await _send(sf, ids["build"])

    assert sent == 1
    assert "Failed at:" not in providers["slack"][0]["message"]


async def test_problems_go_to_the_server_log_when_there_is_no_reporter(sf, providers, caplog):
    import logging

    from app.services.build_notifications import send_failure_notifications

    async with sf() as db:
        ids = await seed_build(db, yaml_content=_yaml("    - missing-channel\n"))
        await db.commit()
    with caplog.at_level(logging.WARNING, logger="app.services.build_notifications"):
        async with sf() as db:
            build = await load_build(db, ids["build"])
            sent = await send_failure_notifications(
                db, build, secrets={}, env_vars={}, builtins=builtins_for(build),
            )

    assert sent == 0
    assert any("missing-channel" in r.getMessage() for r in caplog.records)


def test_default_yaml_fixture_is_valid():
    from app.services.pipeline_compiler import validate_pipeline

    assert validate_pipeline(PIPELINE_YAML) == []
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_build_notifications.py -q`
Expected: `24 failed, 1 passed`, the failures with `ModuleNotFoundError: No module named 'app.services.build_notifications'`. The one that passes only checks that the shared YAML fixture is valid.

- [ ] **Step 4: Implement the module**

Create `backend/app/services/build_notifications.py`:

```python
"""Failure notifications declared in a pipeline's YAML.

A pipeline can name the notification channels that are told when one of its
builds fails::

    notifications:
      on_failure:
        - deploy-alerts
        - channel: ops-email
          recipient: oncall@example.com
          subject: "Deploy failed"
          message: "Build #${{ build.number }} failed: ${{ build.url }}"

The server sends these itself when a build ends as failed, so they also go
out when no agent could run the build. The YAML rules live in
``pipeline_compiler``; this module reads the block and sends the messages.
"""

from __future__ import annotations

import html
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.build import Build, Step
from app.models.notification import NotificationChannel
from app.models.pipeline import Pipeline
from app.services.notification_service import send_notification
from app.services.step_actions.interpolation import interpolate_value

logger = logging.getLogger(__name__)

Report = Callable[[str], Awaitable[None]]
Values = dict[str, dict[str, str]]


@dataclass(frozen=True)
class FailureNotification:
    """One ``on_failure`` entry, in normalized form."""

    channel: str
    message: str | None = None
    subject: str | None = None
    recipient: str | None = None


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def failure_notifications(yaml_content: str | None) -> list[FailureNotification]:
    """Read the ``notifications.on_failure`` entries from pipeline YAML.

    Never raises. Missing, unparseable or malformed input yields an empty
    list, and malformed entries are skipped: the validator is what reports
    mistakes to the author, so this reader only has to be safe.
    """
    try:
        data = yaml.safe_load(yaml_content or "")
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    block = data.get("notifications")
    if not isinstance(block, dict):
        return []
    entries = block.get("on_failure")
    if not isinstance(entries, list):
        return []

    result: list[FailureNotification] = []
    for entry in entries:
        if isinstance(entry, str):
            if entry.strip():
                result.append(FailureNotification(channel=entry.strip()))
        elif isinstance(entry, dict):
            channel = _text(entry.get("channel"))
            if channel:
                result.append(
                    FailureNotification(
                        channel=channel.strip(),
                        message=_text(entry.get("message")),
                        subject=_text(entry.get("subject")),
                        recipient=_text(entry.get("recipient")),
                    )
                )
    return result


def failed_step_of(build: Build) -> tuple[str, str, Step | None]:
    """The first failed step of *build* as ``(stage name, step name, step)``,
    or ``("", "", None)``. Expects ``build.stages`` and their steps loaded."""
    for stage in sorted(build.stages, key=lambda s: s.sort_order):
        for step in sorted(stage.steps, key=lambda s: s.sort_order):
            if step.status == "failed":
                return stage.name, step.name, step
    return "", "", None


def build_url(build_id: Any) -> str:
    base = get_settings().MEGOOCI_PUBLIC_URL.rstrip("/")
    return f"{base}/builds/{build_id}"


def default_subject(values: Values) -> str:
    build = values.get("build", {})
    pipeline = values.get("pipeline", {})
    return f"Build #{build.get('number', '')} of {pipeline.get('name', '')} failed"


def default_message(values: Values) -> str:
    """The message used when an entry does not write its own."""
    build = values.get("build", {})
    lines = [default_subject(values)]

    details = []
    project_name = values.get("project", {}).get("name")
    if project_name:
        details.append(f"Project: {project_name}")
    if build.get("branch"):
        details.append(f"Branch: {build['branch']}")
    if build.get("commit"):
        details.append(f"Commit: {build['commit'][:7]}")
    if details:
        lines.append(" | ".join(details))

    if build.get("failed_step"):
        lines.append(
            f"Failed at: stage \"{build.get('failed_stage', '')}\", "
            f"step \"{build['failed_step']}\""
        )
    if build.get("url"):
        lines.append(build["url"])
    return "\n".join(lines)


def _html_escaped(values: Values) -> Values:
    """Telegram messages are sent as HTML: a ``<`` or ``&`` in a stage, branch
    or pipeline name would make Telegram reject the message."""
    return {
        namespace: {key: html.escape(value, quote=False) for key, value in entries.items()}
        for namespace, entries in values.items()
    }


async def send_failure_notifications(
    db: AsyncSession,
    build: Build,
    *,
    secrets: dict[str, str],
    env_vars: dict[str, str],
    builtins: Values,
    report: Report | None = None,
) -> int:
    """Send every ``on_failure`` notification of the build's pipeline.

    Reads the block from the pipeline's YAML as stored now. Entries are
    independent: a problem with one is passed to *report* (one short line,
    safe to show in the build log) and the rest still go out. Returns the
    number of messages sent.
    """

    async def _report(text: str) -> None:
        if report is not None:
            await report(text)
        else:
            logger.warning("build %s: %s", build.id, text)

    pipeline = await db.get(Pipeline, build.pipeline_id)
    if pipeline is None:
        return 0
    entries = failure_notifications(pipeline.yaml_content)
    if not entries:
        return 0

    stage_name, step_name, _ = failed_step_of(build)
    values: Values = {namespace: dict(entries_) for namespace, entries_ in builtins.items()}
    values.setdefault("pipeline", {}).setdefault("name", pipeline.name)
    values["build"] = {
        **values.get("build", {}),
        "number": str(build.number),
        "status": "failed",
        "failed_stage": stage_name,
        "failed_step": step_name,
        "url": build_url(build.id),
    }

    sent = 0
    for entry in entries:
        channel = await db.scalar(
            select(NotificationChannel).where(NotificationChannel.name == entry.channel)
        )
        if channel is None:
            await _report(
                f"Failure notification not sent: channel '{entry.channel}' was not found."
            )
            continue
        if not channel.enabled:
            await _report(
                f"Failure notification not sent: channel '{entry.channel}' is disabled."
            )
            continue

        shown = _html_escaped(values) if channel.channel_type == "telegram" else values
        message = (
            interpolate_value(entry.message, secrets, env_vars, shown)
            if entry.message
            else default_message(shown)
        )
        subject = (
            interpolate_value(entry.subject, secrets, env_vars, values)
            if entry.subject
            else default_subject(values)
        )

        try:
            delivery = await send_notification(
                db,
                channel.id,
                message,
                subject=subject,
                recipient=entry.recipient,
                build_id=build.id,
            )
        except Exception:
            logger.exception("build %s: failure notification via %s", build.id, entry.channel)
            await db.rollback()
            delivered = False
        else:
            delivered = delivery.status == "sent"

        if delivered:
            sent += 1
        else:
            # The provider's error text is deliberately left out: it can quote
            # the channel's webhook URL or bot token, and the build log is
            # readable by everyone who can see the build.
            await _report(
                f"Failure notification via channel '{entry.channel}' could not be "
                "delivered. An administrator can find the error in the notification "
                "delivery history."
            )
    return sent
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_build_notifications.py -q`
Expected: `25 passed`.

Run the whole suite: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `408 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/build_notifications.py backend/tests/_failure_notifications.py backend/tests/test_build_notifications.py
git commit -m "feat(builds): read and send pipeline failure notifications"
```

---

### Task 3: Send them when a build fails

**Files:**
- Modify: `backend/app/services/build_executor.py` (a new helper above `_send_build_finished_notification`; one call at the end of `_run_build_stages`)
- Test: `backend/tests/test_failure_notifications_executor.py` (new)

**Interfaces:**
- Consumes: `failed_step_of` and `send_failure_notifications` from `app.services.build_notifications` (Task 2); `FakeRedis`, `seed_build`, `seed_channel` from `tests._failure_notifications` (Task 2).
- Consumes (existing, same file): `_emit_system_log(step, db, redis_client, channel, message)`, which adds a `system` log line to a step and publishes it.
- Produces: `async _notify_build_failure(db, redis_client, channel, build, secrets, env_vars, builtins) -> None`. Never raises.

**Where the call goes:** `_run_build_stages` ends by committing `build.status = final_status`, publishing `build_finished`, releasing agent workspaces, and calling `_send_build_finished_notification` (the in-app notice to the user who triggered the build). The new call goes directly after that one. `secrets`, `env_vars`, `builtins`, `channel` and `redis_client` are all local variables at that point, and `build.stages` with their steps are loaded and carry their final statuses.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_failure_notifications_executor.py`:

```python
"""The executor sends failure notifications when, and only when, a build ends failed."""
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._failure_notifications import FakeRedis, seed_build, seed_channel
from tests._rbac import build_inmemory_factory


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture
def slack(monkeypatch):
    sent = []

    async def fake_slack(config, message, recipient):
        sent.append(message)

    monkeypatch.setattr("app.services.notification_service._send_slack", fake_slack)
    return sent


async def _run(sf, monkeypatch, build_id, step_result, *, on_step=None):
    """Run the real executor loop with the step itself faked."""
    from app.services import build_executor
    from app.services.step_actions.base import StepResult

    async def scope(db, build):
        return {}, {}, {"build": {"number": str(build.number), "branch": build.branch or "",
                                  "commit": build.commit_sha or ""},
                        "pipeline": {"name": "deploy-staging"}, "project": {"name": "Inbox Staging"}}

    async def fake_step(*, step, build, **kwargs):
        if on_step is not None:
            await on_step(build)
        return StepResult(**step_result)

    monkeypatch.setattr(build_executor, "_load_scope_context", scope)
    monkeypatch.setattr(build_executor, "_execute_step", fake_step)
    await build_executor._run_build_stages(
        build_id=build_id, claimed_agent_id=uuid.uuid4(),
        session_factory=sf, redis_client=FakeRedis(), channel="build:x:logs",
    )


async def _seed(sf, **kwargs):
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db, build_status="pending", step_status="pending", **kwargs)
        await db.commit()
    return ids


async def _build_status(sf, build_id):
    from app.models.build import Build

    async with sf() as db:
        return (await db.get(Build, build_id)).status


async def test_failed_build_sends_the_notification(sf, slack, monkeypatch):
    from app.models.notification import NotificationDelivery

    ids = await _seed(sf)

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 1, "status": "failed"})

    assert await _build_status(sf, ids["build"]) == "failed"
    assert len(slack) == 1
    assert "Build #428 of deploy-staging failed" in slack[0]
    assert "Failed at: stage \"deploy\", step \"apply manifests\"" in slack[0]
    async with sf() as db:
        row = (await db.execute(select(NotificationDelivery))).scalar_one()
    assert row.build_id == ids["build"] and row.status == "sent"


async def test_no_agent_failure_also_notifies(sf, slack, monkeypatch):
    """A step that fails because no agent was available is still a failed build."""
    ids = await _seed(sf)

    await _run(sf, monkeypatch, ids["build"], {
        "exit_code": 1, "status": "failed",
        "error": "No build agent is available to execute this step.",
    })

    assert len(slack) == 1


async def test_successful_build_sends_nothing(sf, slack, monkeypatch):
    ids = await _seed(sf)

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 0, "status": "success"})

    assert await _build_status(sf, ids["build"]) == "success"
    assert slack == []


async def test_cancelled_build_sends_nothing(sf, slack, monkeypatch):
    from app.models.build import Build

    ids = await _seed(sf)

    async def cancel(build):
        async with sf() as other:
            (await other.get(Build, build.id)).status = "cancelled"
            await other.commit()

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 0, "status": "success"}, on_step=cancel)

    assert await _build_status(sf, ids["build"]) == "cancelled"
    assert slack == []


async def test_unknown_channel_is_reported_in_the_build_log(sf, slack, monkeypatch):
    from app.models.build import LogChunk

    yaml_content = (
        "name: deploy-staging\n"
        "notifications:\n  on_failure:\n    - no-such-channel\n"
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )
    ids = await _seed(sf, yaml_content=yaml_content)

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 1, "status": "failed"})

    async with sf() as db:
        lines = [c.content for c in (await db.execute(
            select(LogChunk).where(LogChunk.step_id == ids["step"], LogChunk.stream == "system")
        )).scalars()]
    assert any("channel 'no-such-channel' was not found" in line for line in lines), lines
    assert await _build_status(sf, ids["build"]) == "failed"


async def test_error_while_sending_never_changes_the_build_result(sf, monkeypatch):
    ids = await _seed(sf)

    async def explode(*args, **kwargs):
        raise RuntimeError("notification subsystem is down")

    monkeypatch.setattr("app.services.build_notifications.send_failure_notifications", explode)

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 1, "status": "failed"})

    assert await _build_status(sf, ids["build"]) == "failed"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_failure_notifications_executor.py -q`
Expected: `3 failed, 3 passed`. The failures are the three tests that expect a message or a log line. The three that pass check that nothing is sent on success or cancel and that a broken sender leaves the build failed, all of which hold while nothing is wired.

- [ ] **Step 3: Add the helper**

In `backend/app/services/build_executor.py`, insert this function immediately above `async def _send_build_finished_notification(`:

```python
async def _notify_build_failure(
    db: AsyncSession,
    redis_client: aioredis.Redis,
    channel: str,
    build: Build,
    secrets: dict[str, str],
    env_vars: dict[str, str],
    builtins: dict[str, dict[str, str]],
) -> None:
    """Send the pipeline's ``notifications.on_failure`` messages.

    Best-effort: whatever goes wrong here must never change the build's
    result or stop the executor from finishing up.
    """
    from app.services.build_notifications import failed_step_of, send_failure_notifications

    try:
        _, _, failed_step = failed_step_of(build)

        async def report(text: str) -> None:
            # Shown under the failed step, where the author is already looking.
            if failed_step is not None:
                await _emit_system_log(
                    failed_step, db, redis_client, channel, f"⚠️ {text}"
                )

        await send_failure_notifications(
            db,
            build,
            secrets=secrets,
            env_vars=env_vars,
            builtins=builtins,
            report=report,
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception(
            "Failure notifications for build %s could not be sent", build.id
        )
        try:
            await db.rollback()
        except Exception:
            pass
```

- [ ] **Step 4: Call it at the end of a failed build**

At the end of `_run_build_stages`, add the call after the existing `_send_build_finished_notification` call:

```python
        await _send_build_finished_notification(
            db, redis_client, build, final_status
        )
        if final_status == "failed":
            await _notify_build_failure(
                db, redis_client, channel, build, secrets, env_vars, builtins
            )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_failure_notifications_executor.py tests/test_build_cancellation.py -q`
Expected: all pass, including `6 passed` from the new file.

Run the whole suite: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `414 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/build_executor.py backend/tests/test_failure_notifications_executor.py
git commit -m "feat(builds): notify the pipeline's channels when a build fails"
```

---

### Task 4: Teach the AI assistant the block

**Files:**
- Modify: `backend/app/api/v1/ai_assistant.py` (`SYSTEM_PROMPT`: five edits)
- Test: `backend/tests/test_failure_notifications_docs.py` (new)

**Interfaces:**
- Consumes: `NOTIFICATION_ENTRY_FIELDS` and `validate_pipeline` (Task 1); `failure_notifications` (Task 2).
- Produces: `SYSTEM_PROMPT` contains a `## Notifications` section between `## Targeting agents (runs_on)` and `## Pipeline Structure`, whose example is a valid pipeline.

`SYSTEM_PROMPT` is an ordinary triple-quoted string. A backslash at the end of a prose line joins it to the next line, which is how the existing prompt wraps long sentences. Lines inside YAML code blocks must not end with a backslash.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_failure_notifications_docs.py`:

```python
"""The AI assistant prompt teaches the notifications block the validator accepts."""

import os
import re
import sys
from unittest.mock import MagicMock

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is an optional dep not installed in the test venv; stub it before
# importing the module under test so the module-level `import litellm` succeeds.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.services.pipeline_compiler import (
    NOTIFICATION_ENTRY_FIELDS,
    validate_pipeline,
)


def _prompt() -> str:
    from app.api.v1.ai_assistant import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _section(heading: str) -> str:
    prompt = _prompt()
    start = prompt.index(heading)
    end = prompt.index("\n## ", start + 1)
    return prompt[start:end]


def _yaml_examples(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)


def test_prompt_has_a_notifications_section_after_runs_on():
    prompt = _prompt()
    assert (
        prompt.index("## Targeting agents (runs_on)")
        < prompt.index("## Notifications")
        < prompt.index("## Pipeline Structure")
    )


def test_notifications_example_is_a_valid_pipeline_with_both_entry_forms():
    examples = _yaml_examples(_section("## Notifications"))
    assert len(examples) == 1
    assert validate_pipeline(examples[0]) == []

    from app.services.build_notifications import failure_notifications

    entries = failure_notifications(examples[0])
    assert [e.channel for e in entries] == ["deploy-alerts", "ops-email"]
    assert entries[1].recipient and entries[1].subject and entries[1].message


def test_notifications_section_names_every_entry_field_and_placeholder():
    section = _section("## Notifications")
    for field in NOTIFICATION_ENTRY_FIELDS:
        assert f"`{field}`" in section, field
    for placeholder in ("${{ build.failed_stage }}", "${{ build.failed_step }}", "${{ build.url }}"):
        assert placeholder in section, placeholder


def test_pipeline_structure_example_shows_the_block_and_still_validates():
    examples = _yaml_examples(_section("## Pipeline Structure"))
    assert len(examples) == 1
    assert "notifications:" in examples[0]
    assert validate_pipeline(examples[0]) == []


def test_notify_step_section_says_it_cannot_report_a_failure():
    prompt = _prompt()
    start = prompt.index("### notify")
    section = prompt[start:prompt.index("\n### ", start + 1)]
    assert "never reports a failure" in section
    assert "`notifications` block" in section


def test_notify_example_uses_a_placeholder_that_exists():
    prompt = _prompt()
    assert "${{ build.commit_sha }}" not in prompt
    assert "Commit: ${{ build.commit }}" in prompt


def test_rules_tell_the_assistant_to_use_the_block_for_failures():
    prompt = _prompt()
    rules = prompt[prompt.index("## Rules"):]
    assert "12. When the user wants to be told about failed builds" in rules
    assert "`notifications` block with `on_failure`" in rules
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_failure_notifications_docs.py -q`
Expected: `7 failed`.

- [ ] **Step 3: Add the notifications section**

Inside `SYSTEM_PROMPT`, insert the following text immediately above the line `## Pipeline Structure`. Insert it exactly, including the trailing blank line:

````text
## Notifications — Tell people when a build fails
Add a top-level `notifications` block (not inside a stage) to send a message \
through a configured channel whenever a build of this pipeline fails. The \
server sends it, so it also goes out when no agent could run the build.

```yaml
version: 1
name: deploy-staging
notifications:
  on_failure:
    - deploy-alerts                     # short form: a channel name
    - channel: ops-email                # full form
      recipient: oncall@example.com     # optional; overrides the channel's default
      subject: "Staging deploy failed"  # optional; used by email channels
      message: |                        # optional; a default is sent if omitted
        Build #${{ build.number }} of ${{ pipeline.name }} failed
        at ${{ build.failed_stage }} / ${{ build.failed_step }}
        ${{ build.url }}
stages:
  - name: deploy
    steps:
      - run: "./deploy.sh"
```

`on_failure` is the only event, and it must be a non-empty list. Each entry is \
either a channel name or a mapping whose only fields are `channel` (required), \
`message`, `subject` and `recipient`. Channels are configured by admins in the \
Notification Channels UI (email, Slack or Telegram) — never invent a channel \
name; ask the user which channel to use if they have not said.

Without `message`, a default is sent: the pipeline name, build number, branch, \
commit, the stage and step that failed, and a link to the build. A custom \
`message` or `subject` can use every placeholder, plus three that exist only \
here: `${{ build.failed_stage }}`, `${{ build.failed_step }}` and \
`${{ build.url }}`. For an email channel set `recipient`; without it the \
message goes to the channel's own sender address. A successful or cancelled \
build sends nothing.

````

- [ ] **Step 4: Show the key in the structure example**

In the `## Pipeline Structure` example, add three lines after the `runs_on` line:

```text
runs_on: linux          # optional — target a specific agent environment
notifications:          # optional — who is told when a build fails
  on_failure:
    - channel-name
```

- [ ] **Step 5: Say that a `notify` step cannot report a failure**

In the `### notify` section, add three lines after the sentence `Channels are configured by admins in the Notification Channels UI.`:

```text
Channels are configured by admins in the Notification Channels UI.
A `notify` step runs only if the build reaches it. A build stops at the first \
failed step, so a `notify` step never reports a failure — use the top-level \
`notifications` block for that.
```

- [ ] **Step 6: Correct the placeholder in the `notify` example**

The `### notify` example uses `${{ build.commit_sha }}`, which is not a placeholder the executor provides (the value is `build.commit`), so it renders as empty. Change that one line:

```text
      Commit: ${{ build.commit }}
```

- [ ] **Step 7: Add rule 12**

At the end of the `## Rules` list, after rule 11's last line (`summary is enough since the YAML comments carry the detail.`), add:

```text
12. When the user wants to be told about failed builds, add the top-level \
`notifications` block with `on_failure` — never a `notify` step at the end of \
the pipeline, which does not run after a failure.
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_failure_notifications_docs.py tests/test_http_request_docs.py -q`
Expected: all pass, including `7 passed` from the new file.

Run the whole suite: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `421 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/v1/ai_assistant.py backend/tests/test_failure_notifications_docs.py
git commit -m "feat(ai): teach the pipeline assistant failure notifications"
```

---

### Task 5: Document the block for people

**Files:**
- Modify: `frontend/src/components/pipeline/docs-panel.tsx` (one icon import, one new `DOCS` entry, three text edits)
- Modify: `README.md` (an example in the Pipeline Example section)

**Interfaces:**
- Consumes: the YAML shape from Task 1. The examples below were checked against the validator.
- Produces: a "Failure Notifications" section in the in-app pipeline docs, between "Target Agent (runs_on)" and "Shell Commands (run)".

- [ ] **Step 1: Import the icon**

In `frontend/src/components/pipeline/docs-panel.tsx`, add `BellRing` to the `lucide-react` import list, after `Bell,`:

```tsx
  Bell,
  BellRing,
```

- [ ] **Step 2: Add the docs section**

Insert this entry into the `DOCS` array, between the `runs_on` entry and the `run` entry:

```tsx
  {
    id: "notifications",
    title: "Failure Notifications",
    icon: <BellRing className="h-4 w-4" />,
    description:
      "Be told when a build of this pipeline fails. Add a top-level notifications block (beside runs_on, not inside a stage) listing the channels to notify; channels are set up by admins under Integrations > Notification Channels. The server sends the message, so it also goes out when no agent could run the build. Each entry is a channel name, or a mapping with channel plus an optional recipient, subject (email) and message. Without a message, a default is sent with the pipeline, build number, branch, commit, the stage and step that failed, and a link to the build. For an email channel set recipient, otherwise the message goes to the channel's own sender address. Successful and cancelled builds send nothing. A channel that does not exist or is disabled is reported in the build log.",
    yaml: `version: 1
name: deploy-staging
notifications:
  on_failure:
    - deploy-alerts                     # short form: a channel name
    - channel: ops-email                # full form
      recipient: oncall@example.com     # optional override
      subject: "Staging deploy failed"  # optional (email only)
      message: |                        # optional; a default is sent if omitted
        Build #\${{ build.number }} of \${{ pipeline.name }} failed
        at \${{ build.failed_stage }} / \${{ build.failed_step }}
        \${{ build.url }}
stages:
  - name: deploy
    steps:
      - run: "./deploy.sh"`,
  },
```

Inside the template literal each placeholder is written `\${{ … }}`; the backslash stops TypeScript from treating `${` as an interpolation, as in the other entries.

- [ ] **Step 3: Mention the block in "Pipeline Structure"**

In the `structure` entry's `description`, replace

```text
an optional runs_on targeting the build agent, and a list of stages. Each stage has steps.
```

with

```text
an optional runs_on targeting the build agent, an optional notifications block naming who is told when a build fails, and a list of stages. Each stage has steps.
```

- [ ] **Step 4: Update the "Send Notification" section**

In the `notify` entry's `description`, after the sentence `Channels are set up by admins under Integrations > Notification Channels.`, add:

```text
 A notify step runs only if the build reaches it: a build stops at the first failed step, so to be told about failed builds use the top-level notifications block (see Failure Notifications).
```

In the same entry's `yaml`, change the placeholder that does not exist:

```tsx
      Commit: \${{ build.commit }}
```

(it currently reads `\${{ build.commit_sha }}`).

- [ ] **Step 5: Type-check**

Run from `frontend/`: `npx tsc --noEmit`
Expected: no output and exit code 0.

- [ ] **Step 6: Update the README**

In `README.md`, in the Pipeline Example section, insert the following immediately above the paragraph that starts `Link the pipeline to a project whose repository points at`:

````markdown
To be told when a build fails, add a top-level `notifications` block. The server sends the message through a channel configured under Notification Channels, so it also goes out when no agent could run the build:

```yaml
name: deploy-staging
notifications:
  on_failure:
    - deploy-alerts                   # a channel name
    - channel: ops-email              # or a mapping
      recipient: oncall@example.com
stages:
  - name: deploy
    steps:
      - run: ./deploy.sh
```

Without a `message`, a default is sent with the pipeline, build number, branch, commit, the stage and step that failed, and a link to the build. A `notify` step at the end of a pipeline cannot do this: a build stops at the first failed step.

````

- [ ] **Step 7: Check it for real**

This needs a running stack with the backend rebuilt from this branch and at least one notification channel configured. If that is unavailable in this environment, do not run this step; report it as not done.

1. Open the pipeline editor's docs panel and expand "Failure Notifications". Expected: the description and example render, with placeholders shown as `${{ build.number }}` without a backslash.
2. Add a `notifications` block naming a real channel to a pipeline whose first step runs `exit 1`, and trigger it. Expected: the build fails, the message arrives on the channel with the pipeline name, build number, the failed stage and step, and a working link.
3. Change the channel name to one that does not exist and trigger again. Expected: the build log shows a line under the failed step saying the channel was not found.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/pipeline/docs-panel.tsx README.md
git commit -m "docs(pipeline): document failure notifications"
```
