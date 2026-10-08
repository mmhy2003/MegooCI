"""The proposal shown to the user: the new YAML and how it differs."""

from __future__ import annotations

import difflib
from dataclasses import asdict, dataclass, field
from typing import Any

CONTEXT_LINES = 3
# Comparing two texts line by line takes time that grows with the product of
# their lengths, and cannot be interrupted. Only the part between the first
# and the last differing line is compared, and only when it is this short;
# a longer part is shown as removed and added whole.
MAX_DIFFED_LINES = 1000

# (tag, old start, old end, new start, new end) with 0-based, end-exclusive
# indexes; tag is "equal", "replace", "delete" or "insert".
_Opcode = tuple[str, int, int, int, int]


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


def _opcodes(old: list[str], new: list[str]) -> list[_Opcode]:
    """How to turn *old* into *new*, covering both from start to end."""
    limit = min(len(old), len(new))
    head = 0
    while head < limit and old[head] == new[head]:
        head += 1
    tail = 0
    while tail < limit - head and old[-1 - tail] == new[-1 - tail]:
        tail += 1
    old_end, new_end = len(old) - tail, len(new) - tail

    opcodes: list[_Opcode] = []
    if head:
        opcodes.append(("equal", 0, head, 0, head))
    if old_end - head > MAX_DIFFED_LINES or new_end - head > MAX_DIFFED_LINES:
        opcodes.append(("replace", head, old_end, head, new_end))
    elif old_end > head or new_end > head:
        matcher = difflib.SequenceMatcher(None, old[head:old_end], new[head:new_end], autojunk=False)
        opcodes.extend(
            (tag, i1 + head, i2 + head, j1 + head, j2 + head)
            for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        )
    if tail:
        opcodes.append(("equal", old_end, len(old), new_end, len(new)))
    return opcodes


def _grouped(opcodes: list[_Opcode], context: int) -> list[list[_Opcode]]:
    """Split *opcodes* into groups of changes that are close together, each
    with up to *context* unchanged lines around it."""
    if not any(tag != "equal" for tag, *_ in opcodes):
        return []
    codes = list(opcodes)
    if codes[0][0] == "equal":
        tag, i1, i2, j1, j2 = codes[0]
        codes[0] = (tag, max(i1, i2 - context), i2, max(j1, j2 - context), j2)
    if codes[-1][0] == "equal":
        tag, i1, i2, j1, j2 = codes[-1]
        codes[-1] = (tag, i1, min(i2, i1 + context), j1, min(j2, j1 + context))

    groups: list[list[_Opcode]] = []
    group: list[_Opcode] = []
    for tag, i1, i2, j1, j2 in codes:
        # A long unchanged stretch ends one group and starts the next.
        if tag == "equal" and i2 - i1 > context * 2:
            group.append((tag, i1, min(i2, i1 + context), j1, min(j2, j1 + context)))
            groups.append(group)
            group = []
            i1, j1 = max(i1, i2 - context), max(j1, j2 - context)
        group.append((tag, i1, i2, j1, j2))
    if group and not (len(group) == 1 and group[0][0] == "equal"):
        groups.append(group)
    return groups


def build_hunks(original: str, updated: str) -> tuple[list[Hunk], int, int]:
    """Hunks with context, and the number of added and removed lines."""
    old_lines, new_lines = _lines(original), _lines(updated)
    hunks: list[Hunk] = []
    added = removed = 0

    for group in _grouped(_opcodes(old_lines, new_lines), CONTEXT_LINES):
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


_MARKS = {"context": " ", "add": "+", "remove": "-"}


def unified_diff(original: str, updated: str) -> str:
    """A plain unified diff, for the model to review its own changes."""
    hunks, _, _ = build_hunks(original, updated)
    if not hunks:
        return ""
    out = ["--- before", "+++ after"]
    for hunk in hunks:
        old_count = sum(1 for line in hunk.lines if line.kind != "add")
        new_count = sum(1 for line in hunk.lines if line.kind != "remove")
        out.append(f"@@ -{hunk.old_start},{old_count} +{hunk.new_start},{new_count} @@")
        out.extend(_MARKS[line.kind] + line.text for line in hunk.lines)
    return "\n".join(out)


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
