"""Trim build logs down to what an agent needs to read."""

from __future__ import annotations

import re
from typing import Any

DEFAULT_TAIL_LINES = 200
MAX_TAIL_LINES = 2000
# Hard cap on returned log text, whatever the line count: one minified or
# progress-bar line can be megabytes long.
MAX_OUTPUT_CHARS = 60_000
_FAILED = "failed"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _steps(build: dict[str, Any]) -> list[dict[str, Any]]:
    return [step for stage in build.get("stages", []) for step in stage.get("steps", [])]


def render_logs(
    build: dict[str, Any],
    chunks: list[dict[str, Any]],
    *,
    step: str | None = None,
    tail_lines: int = DEFAULT_TAIL_LINES,
) -> str:
    """Render log chunks as text, keeping only the last *tail_lines* lines.

    *build* is a ``GET /builds/{id}`` body (stages with steps) and *chunks* a
    ``GET /builds/{id}/logs`` body. Without *step*, a build with failed steps
    shows only those steps; otherwise every step is shown.
    """
    tail_lines = max(1, min(tail_lines, MAX_TAIL_LINES))
    steps = _steps(build)
    header = f"Build #{build.get('number')} — status: {build.get('status')}"

    if step is not None:
        names = [s["name"] for s in steps]
        if step not in names:
            known = ", ".join(names) if names else "(none)"
            return f"{header}\nNo step named '{step}' in this build. Steps: {known}"
        selected = [c for c in chunks if c.get("step_name") == step]
        scope_note = f"Showing step '{step}'."
    else:
        failed = [s for s in steps if s.get("status") == _FAILED]
        if failed:
            failed_ids = {str(s["id"]) for s in failed}
            selected = [c for c in chunks if str(c.get("step_id")) in failed_ids]
            names = ", ".join(s["name"] for s in failed)
            scope_note = f"Showing failed steps only: {names}. Pass `step` to see another step."
        else:
            selected = list(chunks)
            scope_note = "Showing all steps."

    lines: list[tuple[str, str]] = []
    for chunk in selected:
        heading = f"{chunk.get('stage_name', '?')} / {chunk.get('step_name', '?')}"
        content = _ANSI.sub("", str(chunk.get("content") or ""))
        for line in content.splitlines():
            lines.append((heading, line))

    if not lines:
        return f"{header}\n{scope_note}\nNo log output recorded."

    kept = lines[-tail_lines:]
    omitted = len(lines) - len(kept)

    body_lines: list[str] = []
    current: str | None = None
    for heading, line in kept:
        if heading != current:
            body_lines.append(f"== {heading} ==")
            current = heading
        body_lines.append(line)
    body = "\n".join(body_lines)

    preamble = [header, scope_note]
    if omitted:
        preamble.append(
            f"... {omitted} earlier line(s) omitted. "
            f"Raise `tail_lines` (max {MAX_TAIL_LINES}) or pass `step` to see more."
        )
    if len(body) > MAX_OUTPUT_CHARS:
        body = body[-MAX_OUTPUT_CHARS:]
        preamble.append(f"... output truncated to its last {MAX_OUTPUT_CHARS} characters.")
    return "\n".join([*preamble, body])
