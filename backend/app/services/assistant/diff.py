"""The proposal shown to the user: the new YAML and how it differs."""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass, field
from typing import Any

CONTEXT_LINES = 3


@dataclass(frozen=True)
class DiffLine:
    kind: str  # "context" | "add" | "remove"
    old: int | None  # line number in the original, None for added lines
    new: int | None  # line number in the proposal, None for removed lines
    text: str


@dataclass(frozen=True)
class Hunk:
    old_start: int
    new_start: int
    lines: list[DiffLine]


@dataclass(frozen=True)
class Proposal:
    yaml: str
    added: int
    removed: int
    hunks: list[Hunk]
    problems: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _lines(text: str) -> list[str]:
    return text.replace("\r\n", "\n").replace("\r", "\n").splitlines()


def build_hunks(original: str, updated: str) -> tuple[list[Hunk], int, int]:
    """Hunks with context, and the number of added and removed lines."""
    old_lines, new_lines = _lines(original), _lines(updated)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    hunks: list[Hunk] = []
    added = removed = 0

    for group in matcher.get_grouped_opcodes(CONTEXT_LINES):
        lines: list[DiffLine] = []
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                for offset in range(i2 - i1):
                    lines.append(
                        DiffLine("context", i1 + offset + 1, j1 + offset + 1, old_lines[i1 + offset])
                    )
                continue
            for index in range(i1, i2):
                lines.append(DiffLine("remove", index + 1, None, old_lines[index]))
                removed += 1
            for index in range(j1, j2):
                lines.append(DiffLine("add", None, index + 1, new_lines[index]))
                added += 1
        first = group[0]
        hunks.append(Hunk(old_start=first[1] + 1, new_start=first[3] + 1, lines=lines))

    return hunks, added, removed


def unified_diff(original: str, updated: str) -> str:
    """A plain unified diff, for the model to review its own changes."""
    diff = difflib.unified_diff(
        _lines(original), _lines(updated), "before", "after", lineterm="", n=CONTEXT_LINES
    )
    return "\n".join(diff)


def build_proposal(
    original: str | None, updated: str, problems: list[dict[str, Any]] | None = None
) -> Proposal | None:
    """The proposal for changing *original* into *updated*, or None when they
    do not differ line for line."""
    hunks, added, removed = build_hunks(original or "", updated)
    if not hunks:
        return None
    return Proposal(
        yaml=updated, added=added, removed=removed, hunks=hunks, problems=list(problems or [])
    )
