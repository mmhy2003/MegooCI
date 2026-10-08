"""The model/tool loop, driven by a scripted fake model."""

import json

import pytest

from app.services.assistant.document import WorkingDocument
from app.services.assistant.loop import ModelTurn, ToolCall, run_loop
from app.services.assistant.tools import ToolContext

YAML = "name: demo\nstages:\n  - name: build\n    steps:\n      - run: make build\n"


def tool_turn(*calls):
    """A model answer that asks for tools. calls: (id, name, arguments dict)."""
    tool_calls = [ToolCall(id=i, name=n, arguments=json.dumps(a)) for i, n, a in calls]
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in tool_calls
        ],
    }
    return ModelTurn(text=None, tool_calls=tool_calls, message=message)


def final_turn(text):
    return ModelTurn(text=text, tool_calls=[], message={"role": "assistant", "content": text})


class ScriptedModel:
    """Returns the scripted turns in order and records what it was sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.seen = []

    async def __call__(self, messages):
        self.seen.append([dict(m) for m in messages])
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


def ctx(text=YAML):
    return ToolContext(document=WorkingDocument(text), topics={})


async def test_tools_then_answer():
    model = ScriptedModel(
        tool_turn(("c1", "read_lines", {})),
        tool_turn(("c2", "replace_text", {"old": "make build", "new": "make all"}),
                  ("c3", "validate", {})),
        final_turn("  I changed the build command.  "),
    )
    context = ctx()
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    seen_steps = []

    async def on_step(step):
        seen_steps.append(step.label)

    result = await run_loop(messages, model, context, on_step=on_step)

    assert result.reply == "I changed the build command."
    assert result.limit_reached is False and result.model_calls == 3
    assert [s.tool for s in result.steps] == ["read_lines", "replace_text", "validate"]
    assert seen_steps == ["Read lines 1–5", "Replaced text at line 5", "Validated · no problems"]
    assert "make all" in context.document.text


async def test_tool_results_are_fed_back_in_the_chat_format():
    model = ScriptedModel(
        tool_turn(("c1", "search", {"pattern": "build"}), ("c2", "read_lines", {"start": 1, "end": 1})),
        final_turn("done"),
    )
    messages = [{"role": "user", "content": "u"}]

    await run_loop(messages, model, ctx())

    second_call = model.seen[1]
    assert [m["role"] for m in second_call] == ["user", "assistant", "tool", "tool"]
    assert second_call[1]["tool_calls"][0]["id"] == "c1"
    assert second_call[2]["tool_call_id"] == "c1" and "> 3 |" in second_call[2]["content"]
    assert second_call[3] == {"role": "tool", "tool_call_id": "c2", "content": "1 | name: demo"}


async def test_an_answer_without_tools_ends_the_loop_at_once():
    model = ScriptedModel(final_turn("Use `runs_on: linux`."))
    result = await run_loop([{"role": "user", "content": "q"}], model, ctx())
    assert result.reply == "Use `runs_on: linux`." and result.steps == [] and result.model_calls == 1


async def test_failed_tool_calls_are_steps_and_the_loop_continues():
    model = ScriptedModel(
        tool_turn(("c1", "replace_text", {"old": "nope", "new": "x"}),
                  ("c2", "no_such_tool", {})),
        final_turn("Could not find it."),
    )
    result = await run_loop([{"role": "user", "content": "u"}], model, ctx())
    assert [(s.tool, s.ok) for s in result.steps] == [("replace_text", False), ("no_such_tool", False)]
    assert result.reply == "Could not find it."
    assert "was not found" in model.seen[1][2]["content"]
    assert "Unknown tool" in model.seen[1][3]["content"]


async def test_tool_call_limit_stops_the_loop_and_still_answers_every_call():
    endless = [tool_turn((f"a{i}", "read_lines", {}), (f"b{i}", "read_lines", {})) for i in range(10)]
    model = ScriptedModel(*endless)
    messages = [{"role": "user", "content": "u"}]

    result = await run_loop(messages, model, ctx(), max_tool_calls=3)

    assert result.limit_reached is True
    assert len(result.steps) == 3
    assert result.model_calls == 2
    tool_messages = [m for m in messages if m["role"] == "tool"]
    assert len(tool_messages) == 4, "each requested call needs a result, even the refused one"
    assert "limit" in tool_messages[-1]["content"]
    assert result.reply == ""


async def test_time_limit_stops_the_loop():
    now = {"t": 0.0}

    def clock():
        return now["t"]

    class SlowModel(ScriptedModel):
        async def __call__(self, messages):
            now["t"] += 50
            return await super().__call__(messages)

    model = SlowModel(*[tool_turn((f"c{i}", "read_lines", {})) for i in range(10)])
    result = await run_loop([{"role": "user", "content": "u"}], model, ctx(),
                            max_seconds=120, clock=clock)

    assert result.limit_reached is True
    assert result.model_calls == 3  # at 50s, 100s, 150s; the fourth is not started


async def test_a_model_call_that_hangs_is_cut_off_at_the_deadline():
    import asyncio

    async def hanging(messages):
        await asyncio.sleep(30)

    result = await run_loop([{"role": "user", "content": "u"}], hanging, ctx(), max_seconds=0.2)

    assert result.limit_reached is True and result.model_calls == 0


async def test_an_error_on_the_first_model_call_is_raised_for_the_caller_to_fall_back():
    model = ScriptedModel(RuntimeError("provider does not support tools"))
    with pytest.raises(RuntimeError):
        await run_loop([{"role": "user", "content": "u"}], model, ctx())


async def test_an_error_on_a_later_model_call_keeps_what_was_done():
    model = ScriptedModel(
        tool_turn(("c1", "replace_text", {"old": "make build", "new": "make all"})),
        RuntimeError("provider went away"),
    )
    context = ctx()

    result = await run_loop([{"role": "user", "content": "u"}], model, context)

    assert result.limit_reached is True
    assert len(result.steps) == 1 and context.document.changed is True


async def test_a_step_callback_that_fails_does_not_stop_the_loop():
    model = ScriptedModel(tool_turn(("c1", "read_lines", {})), final_turn("ok"))

    async def broken(step):
        raise RuntimeError("client went away")

    result = await run_loop([{"role": "user", "content": "u"}], model, ctx(), on_step=broken)
    assert result.reply == "ok" and len(result.steps) == 1


async def test_empty_final_text_gives_an_empty_reply():
    model = ScriptedModel(final_turn(None))
    result = await run_loop([{"role": "user", "content": "u"}], model, ctx())
    assert result.reply == "" and result.limit_reached is False
