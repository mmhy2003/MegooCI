"""The assistant's working copy of a pipeline's YAML.

Line numbers are 1-based and inclusive. Every operation returns text meant for
the model. A request that cannot be carried out raises ``DocumentError`` with
a message the model can act on.
"""

from __future__ import annotations

MAX_DOCUMENT_BYTES = 256 * 1024  # the validator's limit
MAX_READ_LINES = 400
MAX_SEARCH_MATCHES = 50
_CONTEXT_LINES = 2


class DocumentError(Exception):
    """An editing request that cannot be carried out as asked."""


def _split(text: str) -> list[str]:
    """Lines of *text* without the empty tail a final newline produces."""
    if text == "":
        return []
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    return lines


class WorkingDocument:
    def __init__(self, text: str | None) -> None:
        # The editor works in LF; normalize so edits never mix line endings.
        self.original = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        self._lines = _split(self.original)

    # ── state ────────────────────────────────────────────────────────────

    @property
    def text(self) -> str:
        return "\n".join(self._lines) + "\n" if self._lines else ""

    @property
    def line_count(self) -> int:
        return len(self._lines)

    @property
    def changed(self) -> bool:
        return self._lines != _split(self.original)

    # ── reading ──────────────────────────────────────────────────────────

    def numbered(self, start: int, end: int) -> str:
        """Lines *start*..*end* (already valid), each prefixed with its number."""
        width = len(str(max(end, 1)))
        return "\n".join(
            f"{number:>{width}} | {self._lines[number - 1]}" for number in range(start, end + 1)
        )

    def read_lines(self, start: int = 1, end: int | None = None) -> str:
        if self.line_count == 0:
            return "The document is empty."
        end = self.line_count if end is None else end
        start, end = self._range(start, end)
        note = ""
        if end - start + 1 > MAX_READ_LINES:
            end = start + MAX_READ_LINES - 1
            note = (
                f"\n(showing {MAX_READ_LINES} lines; the document has "
                f"{self.line_count}. Call read_lines again for more.)"
            )
        return self.numbered(start, end) + note

    def search(self, pattern: str) -> tuple[str, int]:
        """Lines containing *pattern*, ignoring case, with one line of context.
        Returns (text, match count).

        The pattern is plain text on purpose: a regular expression can take
        hours on a single line, and nothing can interrupt it.
        """
        if not pattern:
            raise DocumentError("search needs a non-empty pattern.")
        needle = pattern.lower()
        matches = [i for i, line in enumerate(self._lines, 1) if needle in line.lower()]

        if not matches:
            return "No matches.", 0

        shown = matches[:MAX_SEARCH_MATCHES]
        width = len(str(self.line_count))
        blocks = []
        for number in shown:
            first = max(1, number - 1)
            last = min(self.line_count, number + 1)
            blocks.append(
                "\n".join(
                    f"{'>' if n == number else ' '} {n:>{width}} | {self._lines[n - 1]}"
                    for n in range(first, last + 1)
                )
            )
        text = "\n--\n".join(blocks)
        if len(matches) > len(shown):
            text += f"\n(showing the first {len(shown)} of {len(matches)} matches)"
        return text, len(matches)

    # ── editing ──────────────────────────────────────────────────────────

    def replace_text(self, old: str, new: str) -> tuple[str, int]:
        """Replace the single occurrence of *old*. Returns (text, line)."""
        if not old:
            raise DocumentError("replace_text needs a non-empty 'old' text.")
        old = old.replace("\r\n", "\n")
        new = new.replace("\r\n", "\n")
        current = self.text
        count = current.count(old)
        if count == 0:
            raise DocumentError(
                "The text to replace was not found. It must match exactly, including "
                "indentation. Use read_lines or search to see the current text."
            )
        if count > 1:
            lines = []
            position = current.find(old)
            while position != -1:
                lines.append(str(current.count("\n", 0, position) + 1))
                position = current.find(old, position + 1)
            raise DocumentError(
                f"The text to replace appears {count} times (at lines {', '.join(lines)}). "
                "Include more surrounding text so it matches once, or use replace_lines."
            )
        position = current.find(old)
        first_line = current.count("\n", 0, position) + 1
        self._commit(_split(current[:position] + new + current[position + len(old):]))
        last_line = min(self.line_count, first_line + new.count("\n"))
        return self._result(f"Replaced text at line {first_line}.", first_line, last_line), first_line

    def replace_lines(self, start: int, end: int, text: str) -> str:
        if self.line_count == 0:
            raise DocumentError("The document is empty. Use insert_lines or write_document.")
        start, end = self._range(start, end)
        new_lines = _split(text.replace("\r\n", "\n"))
        self._commit(self._lines[: start - 1] + new_lines + self._lines[end:])
        if not new_lines:
            summary = (
                f"Deleted line {start}." if start == end else f"Deleted lines {start}-{end}."
            )
            return self._result(summary, start, start - 1)
        summary = (
            f"Replaced line {start}." if start == end else f"Replaced lines {start}-{end}."
        )
        return self._result(summary, start, start + len(new_lines) - 1)

    def insert_lines(self, after: int, text: str) -> tuple[str, int]:
        """Insert *text* after line *after* (0 = at the top). Returns (text, lines added)."""
        if after < 0 or after > self.line_count:
            raise DocumentError(
                f"'after' must be between 0 and {self.line_count} "
                f"(the document has {self.line_count} lines)."
            )
        new_lines = _split(text.replace("\r\n", "\n"))
        if not new_lines:
            raise DocumentError("insert_lines needs some text to insert.")
        self._commit(self._lines[:after] + new_lines + self._lines[after:])
        summary = f"Inserted {len(new_lines)} line(s) after line {after}."
        return self._result(summary, after + 1, after + len(new_lines)), len(new_lines)

    def write(self, text: str) -> str:
        self._commit(_split(text.replace("\r\n", "\n").replace("\r", "\n")))
        return f"Replaced the whole document. It now has {self.line_count} line(s)."

    # ── helpers ──────────────────────────────────────────────────────────

    def _range(self, start: int, end: int) -> tuple[int, int]:
        if start < 1 or end > self.line_count or start > end:
            raise DocumentError(
                f"Invalid line range {start}-{end}: the document has "
                f"{self.line_count} line(s) and start must not be after end."
            )
        return start, end

    def _commit(self, lines: list[str]) -> None:
        size = sum(len(line.encode("utf-8")) + 1 for line in lines)
        if size > MAX_DOCUMENT_BYTES:
            raise DocumentError(
                f"That edit would make the document larger than {MAX_DOCUMENT_BYTES // 1024} KiB. "
                "It was not applied."
            )
        self._lines = lines

    def _result(self, summary: str, first: int, last: int) -> str:
        """*summary* plus the changed region with a little context, renumbered."""
        if self.line_count == 0:
            return f"{summary} The document is now empty."
        start = max(1, min(first, self.line_count) - _CONTEXT_LINES)
        end = min(self.line_count, max(last, first - 1) + _CONTEXT_LINES)
        if end < start:
            end = start
        return f"{summary} The document now has {self.line_count} line(s):\n" + self.numbered(start, end)
