"""The tools the assistant can call, and how each call is carried out."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.services.assistant.diff import unified_diff
from app.services.assistant.document import DocumentError, WorkingDocument
from app.services.assistant.reference import lookup
from app.services.pipeline_compiler import validate_pipeline_definition

logger = logging.getLogger(__name__)


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


_LINE = {"type": "integer", "minimum": 0}
_TEXT = {"type": "string"}

TOOL_DEFINITIONS: list[dict] = [
    _tool(
        "read_lines",
        "Read lines of the pipeline YAML, each prefixed with its line number. "
        "Without arguments, reads the whole document (up to 400 lines per call).",
        {"start": {**_LINE, "description": "First line, 1-based. Default 1."},
         "end": {**_LINE, "description": "Last line, inclusive. Default: the last line."}},
        [],
    ),
    _tool(
        "search",
        "Find lines containing a piece of text, ignoring case. Returns matching lines "
        "with their numbers and one line of context. The text is matched as written: "
        "it is not a regular expression.",
        {"pattern": {**_TEXT, "description": "The text to find."}},
        ["pattern"],
    ),
    _tool(
        "replace_text",
        "Replace one exact piece of text with another. `old` must match exactly once, "
        "including indentation; include enough surrounding text to make it unique. "
        "Best for small edits.",
        {"old": {**_TEXT, "description": "The exact existing text."},
         "new": {**_TEXT, "description": "The text to put in its place."}},
        ["old", "new"],
    ),
    _tool(
        "replace_lines",
        "Replace a range of lines with new text. Empty text deletes the lines. "
        "Line numbers move after an edit that adds or removes lines, so only the "
        "first such edit in one answer may use line numbers.",
        {"start": {**_LINE, "description": "First line to replace, 1-based."},
         "end": {**_LINE, "description": "Last line to replace, inclusive."},
         "text": {**_TEXT, "description": "The new lines. Empty to delete."}},
        ["start", "end", "text"],
    ),
    _tool(
        "insert_lines",
        "Insert new lines after a line. Use after=0 to insert at the top. "
        "Line numbers move after an edit that adds or removes lines, so only the "
        "first such edit in one answer may use line numbers.",
        {"after": {**_LINE, "description": "Line to insert after; 0 for the top."},
         "text": {**_TEXT, "description": "The lines to insert, correctly indented."}},
        ["after", "text"],
    ),
    _tool(
        "write_document",
        "Replace the whole pipeline YAML. Only for a new pipeline or a full rewrite.",
        {"text": {**_TEXT, "description": "The complete pipeline YAML."}},
        ["text"],
    ),
    _tool(
        "validate",
        "Check the pipeline YAML with the real validator. Returns the problems with "
        "their line numbers, or says there are none. Call this after editing.",
        {},
        [],
    ),
    _tool(
        "show_diff",
        "Show everything you have changed so far, as a unified diff.",
        {},
        [],
    ),
    _tool(
        "reference",
        "Read the documentation for one topic: a step type such as `run` or "
        "`http_request`, or a feature such as `runs_on`, `artifacts`, `notifications`.",
        {"topic": {**_TEXT, "description": "The topic name."}},
        ["topic"],
    ),
    _tool(
        "list_agents",
        "List the registered build agents with their OS, architecture, labels and "
        "whether each is online. Use it when writing `runs_on`.",
        {},
        [],
    ),
]

TOOL_NAMES = [tool["function"]["name"] for tool in TOOL_DEFINITIONS]


@dataclass(frozen=True)
class ToolOutcome:
    text: str  # what the model receives
    label: str  # what the user sees in the chat
    ok: bool = True


@dataclass
class ToolContext:
    document: WorkingDocument
    topics: dict[str, str]
    # Returns the agent list as text. None when the user may not see agents.
    list_agents: Callable[[], Awaitable[str]] | None = None
    # An edit in the model's current answer added or removed lines, so line
    # numbers in the rest of that answer point at the wrong lines.
    lines_moved: bool = False

    def begin_turn(self) -> None:
        """Call before running the tool calls of one model answer."""
        self.lines_moved = False


class _BadArguments(Exception):
    pass


_EDITS = ("replace_text", "replace_lines", "insert_lines", "write_document")
_LINE_NUMBERED_EDITS = ("replace_lines", "insert_lines")
_LINES_MOVED = (
    "Not done: an earlier edit in this same answer added or removed lines, so the "
    "line numbers you used are out of date. Read the lines again and repeat this "
    "edit with the current line numbers."
)


def _integer(args: dict[str, Any], name: str, default: int | None = None) -> int | None:
    if name not in args or args[name] is None:
        return default
    value = args[name]
    if isinstance(value, bool):
        raise _BadArguments(f"'{name}' must be a whole number.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise _BadArguments(f"'{name}' must be a whole number.")


def _required_integer(args: dict[str, Any], name: str) -> int:
    value = _integer(args, name)
    if value is None:
        raise _BadArguments(f"'{name}' is required.")
    return value


def _string(args: dict[str, Any], name: str, *, required: bool = True) -> str:
    if name not in args or args[name] is None:
        if required:
            raise _BadArguments(f"'{name}' is required.")
        return ""
    if not isinstance(args[name], str):
        raise _BadArguments(f"'{name}' must be text.")
    return args[name]


def _shorten(text: str, limit: int = 40) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def run_tool(name: str, arguments: str | None, ctx: ToolContext) -> ToolOutcome:
    """Carry out one tool call. Never raises: every failure becomes a result
    the model can read."""
    if name not in TOOL_NAMES:
        return ToolOutcome(
            f"Unknown tool '{name}'. Available tools: {', '.join(TOOL_NAMES)}.",
            f"Unknown tool {_shorten(name)}",
            ok=False,
        )
    try:
        args = json.loads(arguments) if arguments and arguments.strip() else {}
    except ValueError:
        return ToolOutcome("The arguments were not valid JSON.", f"{name} failed: bad arguments", ok=False)
    if not isinstance(args, dict):
        return ToolOutcome("The arguments must be a JSON object.", f"{name} failed: bad arguments", ok=False)

    try:
        return await _dispatch(name, args, ctx)
    except _BadArguments as exc:
        return ToolOutcome(str(exc), f"{name} failed: bad arguments", ok=False)
    except DocumentError as exc:
        return ToolOutcome(str(exc), f"{name} failed: {_shorten(str(exc), 60)}", ok=False)
    except Exception:
        logger.exception("assistant tool %s failed", name)
        return ToolOutcome("Internal error while running this tool.", f"{name} failed", ok=False)


async def _dispatch(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    if name not in _EDITS:
        return await _run(name, args, ctx)
    if name in _LINE_NUMBERED_EDITS and ctx.lines_moved:
        raise DocumentError(_LINES_MOVED)
    lines_before = ctx.document.line_count
    outcome = await _run(name, args, ctx)
    if ctx.document.line_count != lines_before:
        ctx.lines_moved = True
    return outcome


async def _run(name: str, args: dict[str, Any], ctx: ToolContext) -> ToolOutcome:
    doc = ctx.document

    if name == "read_lines":
        start = _integer(args, "start", 1) or 1
        end = _integer(args, "end")
        text = doc.read_lines(start, end)
        if doc.line_count == 0:
            return ToolOutcome(text, "Read the document · empty")
        last = min(end or doc.line_count, doc.line_count)
        return ToolOutcome(text, f"Read lines {start}–{last}")

    if name == "search":
        pattern = _string(args, "pattern")
        text, count = doc.search(pattern)
        noun = "match" if count == 1 else "matches"
        return ToolOutcome(text, f"Searched “{_shorten(pattern)}” · {count} {noun}")

    if name == "replace_text":
        text, line = doc.replace_text(_string(args, "old"), _string(args, "new"))
        return ToolOutcome(text, f"Replaced text at line {line}")

    if name == "replace_lines":
        start, end = _required_integer(args, "start"), _required_integer(args, "end")
        new_text = _string(args, "text")
        text = doc.replace_lines(start, end, new_text)
        verb = "Replaced" if new_text.strip() else "Deleted"
        span = f"line {start}" if start == end else f"lines {start}–{end}"
        return ToolOutcome(text, f"{verb} {span}")

    if name == "insert_lines":
        after = _required_integer(args, "after")
        text, added = doc.insert_lines(after, _string(args, "text"))
        noun = "line" if added == 1 else "lines"
        return ToolOutcome(text, f"Inserted {added} {noun} after line {after}")

    if name == "write_document":
        text = doc.write(_string(args, "text"))
        return ToolOutcome(text, f"Rewrote the document · {doc.line_count} lines")

    if name == "validate":
        problems = validate_pipeline_definition(doc.text)
        if not problems:
            return ToolOutcome("No problems.", "Validated · no problems")
        lines = [
            f"line {p.line}: {p.message}" if p.line is not None else p.message for p in problems
        ]
        noun = "problem" if len(problems) == 1 else "problems"
        return ToolOutcome("\n".join(lines), f"Validated · {len(problems)} {noun}")

    if name == "show_diff":
        diff = unified_diff(doc.original, doc.text)
        return ToolOutcome(diff or "No changes yet.", "Reviewed its changes")

    if name == "reference":
        topic = _string(args, "topic")
        found = lookup(ctx.topics, topic)
        if found is None:
            return ToolOutcome(
                f"Unknown topic '{topic}'. Topics: {', '.join(ctx.topics)}.",
                f"Looked up “{_shorten(topic)}” · not found",
                ok=False,
            )
        return ToolOutcome(found, f"Looked up “{_shorten(topic)}”")

    # list_agents
    if ctx.list_agents is None:
        return ToolOutcome(
            "The agent list is not available: this user does not have permission to see agents.",
            "Listed agents · not permitted",
            ok=False,
        )
    return ToolOutcome(await ctx.list_agents(), "Listed agents")
