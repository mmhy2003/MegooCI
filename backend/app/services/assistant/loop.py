"""The model/tool loop: call the model, run the tools it asks for, repeat."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.assistant.tools import ToolContext, run_tool

logger = logging.getLogger(__name__)

MAX_TOOL_CALLS = 16
MAX_SECONDS = 120.0


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str | None  # JSON text, as the model produced it


@dataclass(frozen=True)
class ModelTurn:
    """One answer from the model."""

    text: str | None
    tool_calls: list[ToolCall]
    # The assistant message to append to the conversation before the tool
    # results: the provider's own message object, or a dict in the chat format.
    message: Any


@dataclass(frozen=True)
class Step:
    tool: str
    label: str
    ok: bool


@dataclass
class LoopResult:
    reply: str = ""
    steps: list[Step] = field(default_factory=list)
    limit_reached: bool = False
    model_calls: int = 0
    # What a model call after the first raised, when that ended the loop.
    error: Exception | None = None


Complete = Callable[[list[Any]], Awaitable[ModelTurn]]
OnStep = Callable[[Step], Awaitable[None]]

_LIMIT_TEXT = "The tool-call limit for this request was reached. This call was not run."


async def run_loop(
    messages: list[Any],
    complete: Complete,
    ctx: ToolContext,
    *,
    on_step: OnStep | None = None,
    max_tool_calls: int = MAX_TOOL_CALLS,
    max_seconds: float = MAX_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> LoopResult:
    """Run the conversation in *messages* (which is extended in place) until
    the model answers without asking for a tool, or a limit is reached.

    An exception from the first model call is raised, so the caller can fall
    back to a call without tools. A later one ends the loop with what has been
    done so far, and is kept in the result's ``error``.
    """
    result = LoopResult()
    deadline = clock() + max_seconds
    tool_calls_used = 0

    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            result.limit_reached = True
            break
        try:
            turn = await asyncio.wait_for(complete(messages), timeout=remaining)
        except asyncio.TimeoutError:
            result.limit_reached = True
            break
        except Exception as exc:
            if result.model_calls == 0:
                raise
            logger.exception("assistant model call failed mid-loop")
            result.error = exc
            result.limit_reached = True
            break
        result.model_calls += 1

        if not turn.tool_calls:
            result.reply = (turn.text or "").strip()
            break

        messages.append(turn.message)
        ctx.begin_turn()
        for call in turn.tool_calls:
            if tool_calls_used >= max_tool_calls:
                # Every tool call must still be answered, or the next model
                # call would be rejected by the provider.
                result.limit_reached = True
                messages.append({"role": "tool", "tool_call_id": call.id, "content": _LIMIT_TEXT})
                continue
            tool_calls_used += 1
            outcome = await run_tool(call.name, call.arguments, ctx)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": outcome.text})
            step = Step(tool=call.name, label=outcome.label, ok=outcome.ok)
            result.steps.append(step)
            if on_step is not None:
                try:
                    await on_step(step)
                except Exception:
                    logger.exception("assistant step callback failed")
        if result.limit_reached:
            break

    return result
