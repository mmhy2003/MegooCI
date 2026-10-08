# AI Assistant Editing Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the pipeline assistant tools to read, edit and validate a working copy of the pipeline YAML, and show the user its changes as a diff they apply or discard.

**Architecture:** A new backend package, `app.services.assistant`, holds the working copy, the diff, the documentation topics, the tool definitions and the model/tool loop; none of it calls an AI provider or the database. `ai_assistant.py` supplies both: it calls the model with the tools attached, falls back to today's single call when the model will not take tools, and turns the result into a proposal (new YAML, hunks, validator problems). The streaming endpoint sends one event per tool call and a final event with the response. The chat panel switches to the streaming endpoint and renders the proposal in a new review card.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2 async, LiteLLM (`acompletion` with `tools`), `difflib`, pytest with `asyncio_mode=auto` and in-memory SQLite (`tests/_rbac.py`); Next.js 16 / React 19 / TypeScript / Tailwind for the panel.

**Spec:** `docs/superpowers/specs/2026-10-08-ai-assistant-tools-design.md`

## Global Constraints

- **Branch:** all work happens on `feat/ai-assistant-tools`. Never commit to `main`.
- **Commits:** conventional style (`feat(ai): …`, `docs(ai): …`). End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Always on.** There is no setting, environment variable or flag for tool mode. Do not add one.
- **Tools are tried for every model.** Do not ask LiteLLM whether the model supports tools (`supports_function_calling` answers "no" for custom and unknown models). The first model call carries the tools; if it fails, the request is retried once without them.
- **No fallback for failures a retry cannot fix:** `AuthenticationError`, `APIConnectionError`, `Timeout`, `RateLimitError` from `litellm.exceptions` are raised at once.
- **Nothing reaches the editor without the user.** The server only returns a proposal. The panel changes the editor only on Apply or Undo.
- **The working copy lives for one request, in memory.** Nothing is stored.
- **Tools never raise.** Every failure of a tool is a text result the model can read, and a step with `ok: false`.
- **Limits:** 16 tool calls and 120 seconds per request; 400 lines per `read_lines`; 50 matches per `search`; 256 KiB per document; 50 agents per `list_agents`.
- **`SYSTEM_PROMPT` is not edited.** The tool-mode prompt and the reference topics are cut from it at import time.
- **`list_agents` needs the global `agents.read` permission** and opens its own database session.
- **Backend tests:** run from `backend/` with `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`. The suite has 432 tests before this plan and 557 after it.
- **LiteLLM is not installed in the test venv.** Test files that import `app.api.v1.ai_assistant` stub it first, as `tests/test_ai_assistant_scoped.py` does. Tests replace `litellm.acompletion` and `litellm.exceptions` on the module's `litellm` object.
- **Frontend gate:** `npx tsc --noEmit` from `frontend/`. There is no frontend unit-test harness, and `npm run lint` is broken; do not use it.
- **Colours:** use the theme tokens `success`, `warning` and `destructive` (`frontend/COLORS.md`), not raw Tailwind colours.
- **Line endings:** `ai_assistant.py`, `api.ts` and `README.md` use CRLF in the working tree. Keep them: use the Edit tool, not a script that rewrites the file with LF.
- **Paths in commands:** `git` commands use paths relative to the repository root. Commands are written for Git Bash.

## Review Focus

Inputs the spec implies but does not spell out. Each has a test in the task that owns the code.

1. **A model or endpoint that rejects a request carrying tools.** Expected: one retry without tools, `mode: "legacy"`, and the same review card. Pinned in Task 6 (`test_a_provider_that_rejects_tools_falls_back_to_one_call_without_them`).
2. **A model that accepts tools, ignores them, and writes the whole pipeline in its reply.** Expected: that YAML becomes the proposal. A short snippet in an answer must not: it would replace the whole editor. Pinned in Task 6 (`test_a_model_that_ignores_the_tools_and_writes_the_pipeline_still_gets_a_proposal`, `test_a_snippet_in_an_answer_is_not_a_proposal`).
3. **A wrong API key, an unreachable provider, a timeout or a rate limit.** Expected: one model call, not two. Pinned in Task 6 (`test_failures_a_retry_cannot_fix_are_reported_without_a_second_call`).
4. **The browser goes away mid-request.** Expected: the model call is cancelled, so the request stops costing money. Pinned in Task 6 (`test_closing_the_stream_cancels_the_model_call`).
5. **A model call that is silent for a long time.** The Next.js proxy (`proxyTimeout: 120_000`) and tunnels close a silent connection. Expected: the stream sends a comment line every 15 seconds. Pinned in Task 6 (`test_stream_sends_keep_alive_comments_while_the_model_is_silent`).
6. **`list_agents` reports a stale agent as offline.** Working that out must not change the agent's row. Pinned in Task 6 (`test_listing_agents_does_not_change_them`).
7. **The model asks for more tool calls than the limit allows, several in one answer.** Every call must still get a result, or the provider rejects the next request. Pinned in Task 5 (`test_tool_call_limit_stops_the_loop_and_still_answers_every_call`).
8. **Editor content with CRLF line endings or no final newline.** Expected: not counted as a change; no proposal for it. Pinned in Task 1 (`test_crlf_input_is_normalized_and_not_counted_as_a_change`) and Task 2 (`test_a_change_only_in_line_endings_or_final_newline_gives_no_proposal`).
9. **A stream chunk that ends in the middle of an event or of a multi-byte character.** The reader in Task 7 buffers until a blank line and decodes with `stream: true`. This was checked with a throwaway script while planning; there is no frontend test harness to pin it.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/services/assistant/__init__.py` | Create | Package description. |
| `backend/app/services/assistant/document.py` | Create | The working copy: read, search, replace, insert, write. |
| `backend/app/services/assistant/diff.py` | Create | Hunks, counts, the proposal, a unified diff for the model. |
| `backend/app/services/assistant/reference.py` | Create | Topics cut from the full prompt; the tool-mode prompt. |
| `backend/app/services/assistant/tools.py` | Create | Tool definitions for the model; running one tool call. |
| `backend/app/services/assistant/loop.py` | Create | Call the model, run its tools, repeat, within limits. |
| `backend/app/api/v1/ai_assistant.py` | Modify | Response models, the two conversations, the provider calls, fallback, proposal, both endpoints. |
| `frontend/src/lib/api.ts` | Modify | Response types and `aiAssistantApi.stream`. |
| `frontend/src/components/pipeline/ai-proposal-card.tsx` | Create | The review card. |
| `frontend/src/components/pipeline/ai-assistant-panel.tsx` | Modify | Streaming, step list, card states, undo. |
| `README.md` | Modify | One feature bullet. |
| `backend/tests/test_assistant_document.py` | Create | Working-copy tests. |
| `backend/tests/test_assistant_diff.py` | Create | Diff and proposal tests. |
| `backend/tests/test_assistant_reference.py` | Create | Topic and prompt tests. |
| `backend/tests/test_assistant_tools.py` | Create | Tool dispatch tests. |
| `backend/tests/test_assistant_loop.py` | Create | Loop tests with a scripted model. |
| `backend/tests/test_ai_assistant_tools.py` | Create | Endpoint tests with a scripted provider. |

---

### Task 1: The working copy

**Files:**
- Create: `backend/app/services/assistant/__init__.py`
- Create: `backend/app/services/assistant/document.py`
- Test: `backend/tests/test_assistant_document.py` (new)

**Interfaces:**
- Produces: `MAX_DOCUMENT_BYTES = 256 * 1024`, `MAX_READ_LINES = 400`, `MAX_SEARCH_MATCHES = 50`.
- Produces: `DocumentError(Exception)` — its message is written for the model.
- Produces: `WorkingDocument(text: str | None)` with
  - `original: str` and `text: str` — both with LF line endings and a final newline (empty string for an empty document);
  - `line_count: int`, `changed: bool`;
  - `read_lines(start: int = 1, end: int | None = None) -> str`;
  - `search(pattern: str, regex: bool = False) -> tuple[str, int]` — text for the model, number of matches;
  - `replace_text(old: str, new: str) -> tuple[str, int]` — text for the model, line of the change;
  - `replace_lines(start: int, end: int, text: str) -> str`;
  - `insert_lines(after: int, text: str) -> tuple[str, int]` — text for the model, number of lines added;
  - `write(text: str) -> str`.
- Line numbers are 1-based and inclusive. Every method raises `DocumentError` for input it cannot act on, and leaves the document unchanged when it does.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_assistant_document.py`:

```python
"""The assistant's working copy: reading and editing by line and by text."""

import pytest

from app.services.assistant.document import (
    MAX_DOCUMENT_BYTES,
    MAX_READ_LINES,
    MAX_SEARCH_MATCHES,
    DocumentError,
    WorkingDocument,
)

YAML = (
    "name: demo\n"
    "stages:\n"
    "  - name: build\n"
    "    steps:\n"
    "      - run: make build\n"
    "  - name: deploy\n"
    "    steps:\n"
    "      - run: make deploy\n"
)


def doc(text=YAML):
    return WorkingDocument(text)


# ── state ───────────────────────────────────────────────────────────────

def test_unedited_document_is_unchanged_and_round_trips():
    d = doc()
    assert d.line_count == 8
    assert d.text == YAML
    assert d.changed is False


def test_crlf_input_is_normalized_and_not_counted_as_a_change():
    d = WorkingDocument(YAML.replace("\n", "\r\n"))
    assert d.line_count == 8
    assert d.text == YAML
    assert d.changed is False


def test_missing_final_newline_is_not_a_change():
    d = WorkingDocument(YAML.rstrip("\n"))
    assert d.changed is False
    assert d.text == YAML


def test_empty_and_none_documents():
    for d in (WorkingDocument(""), WorkingDocument(None)):
        assert d.line_count == 0 and d.text == "" and d.changed is False
        assert d.read_lines() == "The document is empty."


# ── reading ─────────────────────────────────────────────────────────────

def test_read_lines_numbers_each_line():
    assert doc().read_lines(2, 3) == "2 | stages:\n3 |   - name: build"


def test_read_lines_defaults_to_the_whole_document():
    out = doc().read_lines()
    assert out.splitlines()[0] == "1 | name: demo"
    assert out.splitlines()[-1] == "8 |       - run: make deploy"


def test_read_lines_pads_numbers_to_the_same_width():
    d = WorkingDocument("\n".join(f"l{i}" for i in range(1, 13)) + "\n")
    assert d.read_lines(9, 10) == " 9 | l9\n10 | l10"


@pytest.mark.parametrize("start, end", [(0, 2), (3, 2), (1, 9), (9, 9), (-1, 1)])
def test_read_lines_rejects_bad_ranges_and_states_the_length(start, end):
    with pytest.raises(DocumentError) as exc:
        doc().read_lines(start, end)
    assert "8 line(s)" in str(exc.value)


def test_read_lines_is_capped():
    d = WorkingDocument("\n".join(f"l{i}" for i in range(1, MAX_READ_LINES + 51)) + "\n")
    out = d.read_lines()
    assert f"{MAX_READ_LINES} | l{MAX_READ_LINES}" in out
    assert f"l{MAX_READ_LINES + 1}\n" not in out + "\n"
    assert f"the document has {MAX_READ_LINES + 50}" in out


def test_search_is_case_insensitive_and_shows_context():
    text, count = doc().search("DEPLOY")
    assert count == 2
    assert "> 6 |   - name: deploy" in text
    assert "  5 |       - run: make build" in text  # context line before
    assert "> 8 |       - run: make deploy" in text


def test_search_with_regex():
    text, count = doc().search(r"^\s+- run: make \w+$", regex=True)
    assert count == 2 and "> 5 |" in text and "> 8 |" in text


def test_search_reports_no_matches_and_rejects_bad_input():
    assert doc().search("nothing-here") == ("No matches.", 0)
    with pytest.raises(DocumentError):
        doc().search("")
    with pytest.raises(DocumentError) as exc:
        doc().search("(unclosed", regex=True)
    assert "Invalid regular expression" in str(exc.value)


def test_search_is_capped():
    d = WorkingDocument("x\n" * (MAX_SEARCH_MATCHES + 10))
    text, count = d.search("x")
    assert count == MAX_SEARCH_MATCHES + 10
    assert f"showing the first {MAX_SEARCH_MATCHES} of {MAX_SEARCH_MATCHES + 10}" in text


# ── replace_text ────────────────────────────────────────────────────────

def test_replace_text_replaces_the_single_match_and_shows_the_region():
    d = doc()
    out, line = d.replace_text("make build", "make build -j4")
    assert line == 5
    assert d.text == YAML.replace("make build", "make build -j4")
    assert d.changed is True
    assert out.startswith("Replaced text at line 5.")
    assert "5 |       - run: make build -j4" in out
    assert "3 |   - name: build" in out and "7 |     steps:" in out  # two lines of context


def test_replace_text_can_span_lines_and_change_the_line_count():
    d = doc()
    d.replace_text(
        "      - run: make build\n",
        "      - run: make deps\n      - run: make build\n",
    )
    assert d.line_count == 9
    assert d.text.splitlines()[4:6] == ["      - run: make deps", "      - run: make build"]


def test_replace_text_at_the_very_start_and_end():
    d = doc()
    d.replace_text("name: demo", "name: renamed")
    d.replace_text("make deploy\n", "make deploy --prod\n")
    assert d.text.startswith("name: renamed\n")
    assert d.text.endswith("make deploy --prod\n")


def test_replace_text_not_found():
    d = doc()
    with pytest.raises(DocumentError) as exc:
        d.replace_text("make test", "x")
    assert "was not found" in str(exc.value)
    assert d.changed is False


def test_replace_text_ambiguous_lists_the_lines():
    d = doc()
    with pytest.raises(DocumentError) as exc:
        d.replace_text("    steps:", "    jobs:")
    assert "appears 2 times (at lines 4, 7)" in str(exc.value)
    assert d.changed is False


def test_replace_text_needs_old_text_and_is_whitespace_exact():
    with pytest.raises(DocumentError):
        doc().replace_text("", "x")
    with pytest.raises(DocumentError):
        doc().replace_text("- run:  make build", "x")  # two spaces: no match


# ── line edits ──────────────────────────────────────────────────────────

def test_replace_lines_swaps_a_range():
    d = doc()
    out = d.replace_lines(5, 5, "      - run: make all\n      - run: make check")
    assert d.line_count == 9
    assert d.text.splitlines()[4:6] == ["      - run: make all", "      - run: make check"]
    assert out.startswith("Replaced line 5.")
    assert "5 |       - run: make all" in out


def test_replace_lines_with_empty_text_deletes():
    d = doc()
    out = d.replace_lines(6, 8, "")
    assert d.line_count == 5
    assert "deploy" not in d.text
    assert out.startswith("Deleted lines 6-8.")


def test_deleting_everything_leaves_an_empty_document():
    d = doc()
    out = d.replace_lines(1, 8, "")
    assert d.line_count == 0 and d.text == ""
    assert "now empty" in out


def test_replace_lines_rejects_bad_ranges_and_an_empty_document():
    with pytest.raises(DocumentError):
        doc().replace_lines(7, 9, "x")
    with pytest.raises(DocumentError):
        doc().replace_lines(3, 2, "x")
    with pytest.raises(DocumentError):
        WorkingDocument("").replace_lines(1, 1, "x")


def test_insert_lines_after_a_line_at_the_top_and_at_the_end():
    d = doc()
    out, added = d.insert_lines(5, "      - run: make test\n")
    assert added == 1 and d.text.splitlines()[5] == "      - run: make test"
    assert out.startswith("Inserted 1 line(s) after line 5.")
    d.insert_lines(0, "version: 1")
    assert d.text.startswith("version: 1\nname: demo\n")
    d.insert_lines(d.line_count, "# end")
    assert d.text.endswith("# end\n")


def test_insert_into_an_empty_document():
    d = WorkingDocument("")
    d.insert_lines(0, "name: new\nstages: []\n")
    assert d.text == "name: new\nstages: []\n" and d.changed is True


def test_insert_lines_rejects_bad_positions_and_empty_text():
    with pytest.raises(DocumentError) as exc:
        doc().insert_lines(9, "x")
    assert "between 0 and 8" in str(exc.value)
    with pytest.raises(DocumentError):
        doc().insert_lines(-1, "x")
    with pytest.raises(DocumentError):
        doc().insert_lines(2, "")


def test_write_replaces_everything():
    d = doc()
    out = d.write("name: other\nstages: []")
    assert d.text == "name: other\nstages: []\n"
    assert "2 line(s)" in out


def test_edits_with_crlf_text_do_not_leave_carriage_returns():
    d = doc()
    d.insert_lines(1, "env:\r\n  A: b\r\n")
    d.replace_text("make build", "make\r\nbuild")
    assert "\r" not in d.text


def test_an_edit_that_would_exceed_the_size_limit_is_refused_and_not_applied():
    d = doc()
    with pytest.raises(DocumentError) as exc:
        d.write("x" * (MAX_DOCUMENT_BYTES + 1))
    assert "larger than 256 KiB" in str(exc.value)
    assert d.text == YAML
    with pytest.raises(DocumentError):
        d.insert_lines(1, "y" * (MAX_DOCUMENT_BYTES + 1))
    assert d.changed is False


def test_changing_back_to_the_original_is_not_a_change():
    d = doc()
    d.replace_text("make build", "make all")
    d.replace_text("make all", "make build")
    assert d.changed is False
```

- [ ] **Step 2: Run the tests and see them fail**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_document.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.services.assistant'`.

- [ ] **Step 3: Write the package and the working copy**

Create `backend/app/services/assistant/__init__.py`:

```python
"""Editing tools for the AI pipeline assistant.

The assistant works on a private copy of the pipeline YAML through a small set
of tools, and the result is offered to the user as a diff:

- ``document``  — the working copy and its editing operations
- ``diff``      — the proposal: new YAML, hunks, counts
- ``reference`` — documentation topics cut from the full system prompt
- ``tools``     — tool definitions for the model and their dispatch
- ``loop``      — the model/tool loop

Nothing here calls an AI provider or touches the database; the API module
supplies both.
"""
```

Create `backend/app/services/assistant/document.py`:

```python
"""The assistant's working copy of a pipeline's YAML.

Line numbers are 1-based and inclusive. Every operation returns text meant for
the model. A request that cannot be carried out raises ``DocumentError`` with
a message the model can act on.
"""

from __future__ import annotations

import re

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

    def search(self, pattern: str, regex: bool = False) -> tuple[str, int]:
        """Matching lines with one line of context. Returns (text, match count)."""
        if not pattern:
            raise DocumentError("search needs a non-empty pattern.")
        if regex:
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise DocumentError(f"Invalid regular expression: {exc}") from exc
            matches = [i for i, line in enumerate(self._lines, 1) if compiled.search(line)]
        else:
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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_document.py
```

Expected: `34 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/assistant/__init__.py backend/app/services/assistant/document.py backend/tests/test_assistant_document.py
git commit -m "feat(ai): working copy for the assistant's YAML edits

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The diff and the proposal

**Files:**
- Create: `backend/app/services/assistant/diff.py`
- Test: `backend/tests/test_assistant_diff.py` (new)

**Interfaces:**
- Produces: dataclasses `DiffLine(kind, old, new, text)` (`kind` is `"context"`, `"add"` or `"remove"`; `old`/`new` are 1-based line numbers or `None`), `Hunk(old_start, new_start, lines)`, `Proposal(yaml, added, removed, hunks, problems)` with `to_dict()`.
- Produces: `build_hunks(original: str, updated: str) -> tuple[list[Hunk], int, int]` — hunks with three lines of context, lines added, lines removed.
- Produces: `unified_diff(original: str, updated: str) -> str` — for the `show_diff` tool; empty when nothing changed.
- Produces: `build_proposal(original: str | None, updated: str | None, problems: list[dict] | None = None) -> Proposal | None` — `None` when the two texts are the same apart from line endings and a final newline.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_assistant_diff.py`:

```python
"""The proposal: hunks, counts and line numbers."""

from app.services.assistant.diff import build_hunks, build_proposal, unified_diff

OLD = "".join(f"line {i}\n" for i in range(1, 21))


def test_identical_text_gives_no_proposal():
    assert build_proposal(OLD, OLD) is None
    assert build_proposal(None, "") is None


def test_a_change_only_in_line_endings_or_final_newline_gives_no_proposal():
    assert build_proposal(OLD.replace("\n", "\r\n"), OLD) is None
    assert build_proposal(OLD.rstrip("\n"), OLD) is None


def test_an_inserted_line_is_one_hunk_with_three_lines_of_context():
    new = OLD.replace("line 10\n", "line 10\nadded\n")
    proposal = build_proposal(OLD, new)

    assert (proposal.added, proposal.removed) == (1, 0)
    assert proposal.yaml == new
    assert len(proposal.hunks) == 1
    hunk = proposal.hunks[0]
    assert (hunk.old_start, hunk.new_start) == (8, 8)
    assert [line.kind for line in hunk.lines] == ["context"] * 3 + ["add"] + ["context"] * 3
    added = hunk.lines[3]
    assert (added.old, added.new, added.text) == (None, 11, "added")
    after = hunk.lines[4]
    assert (after.old, after.new, after.text) == (11, 12, "line 11")


def test_a_replaced_line_is_a_remove_followed_by_an_add():
    proposal = build_proposal(OLD, OLD.replace("line 5\n", "line five\n"))
    kinds = [(l.kind, l.old, l.new, l.text) for l in proposal.hunks[0].lines if l.kind != "context"]
    assert kinds == [("remove", 5, None, "line 5"), ("add", None, 5, "line five")]
    assert (proposal.added, proposal.removed) == (1, 1)


def test_a_removed_line():
    proposal = build_proposal(OLD, OLD.replace("line 5\n", ""))
    assert (proposal.added, proposal.removed) == (0, 1)
    removed = [l for l in proposal.hunks[0].lines if l.kind == "remove"]
    assert [(l.old, l.new) for l in removed] == [(5, None)]


def test_distant_changes_are_separate_hunks_and_near_ones_merge():
    far = OLD.replace("line 2\n", "two\n").replace("line 18\n", "eighteen\n")
    assert len(build_proposal(OLD, far).hunks) == 2
    near = OLD.replace("line 2\n", "two\n").replace("line 5\n", "five\n")
    assert len(build_proposal(OLD, near).hunks) == 1


def test_a_new_document_is_all_additions():
    proposal = build_proposal("", "name: x\nstages: []\n")
    assert (proposal.added, proposal.removed) == (2, 0)
    assert [(l.kind, l.new) for l in proposal.hunks[0].lines] == [("add", 1), ("add", 2)]
    assert (proposal.hunks[0].old_start, proposal.hunks[0].new_start) == (1, 1)


def test_emptying_a_document_is_all_removals():
    proposal = build_proposal("a\nb\n", "")
    assert (proposal.added, proposal.removed) == (0, 2)


def test_to_dict_is_json_ready_and_carries_problems():
    import json

    proposal = build_proposal("a\n", "b\n", [{"message": "bad", "line": 1}])
    data = proposal.to_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["problems"] == [{"message": "bad", "line": 1}]
    assert data["hunks"][0]["lines"][0] == {"kind": "remove", "old": 1, "new": None, "text": "a"}


def test_unified_diff_for_the_model():
    text = unified_diff(OLD, OLD.replace("line 5\n", "line five\n"))
    assert "-line 5" in text and "+line five" in text and text.startswith("--- before")
    assert unified_diff(OLD, OLD) == ""


def test_build_hunks_counts_match_the_lines():
    hunks, added, removed = build_hunks(OLD, OLD.replace("line 3\n", "x\ny\n").replace("line 15\n", ""))
    kinds = [l.kind for h in hunks for l in h.lines]
    assert kinds.count("add") == added == 2
    assert kinds.count("remove") == removed == 2
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_diff.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.services.assistant.diff'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/assistant/diff.py`:

```python
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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_diff.py
```

Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/assistant/diff.py backend/tests/test_assistant_diff.py
git commit -m "feat(ai): diff and proposal for assistant changes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Reference topics and the tool-mode prompt

**Files:**
- Create: `backend/app/services/assistant/reference.py`
- Modify: `backend/app/api/v1/ai_assistant.py` (one import; two constants after `SYSTEM_PROMPT`)
- Test: `backend/tests/test_assistant_reference.py` (new)

**Interfaces:**
- Produces: `split_topics(prompt: str) -> dict[str, str]` — one topic per `### <step>` heading under `## Available Step Types`, plus `artifacts`, `runs_on`, `notifications`, `structure`.
- Produces: `build_tool_prompt(prompt: str, topics: dict[str, str]) -> str` — the prompt's introduction, its `## Pipeline Structure` and `## Placeholders` sections, an index of the topics, and `TOOL_RULES`.
- Produces: `lookup(topics: dict[str, str], topic: str) -> str | None` — ignores case and surrounding space.
- Produces, in `ai_assistant.py`: `REFERENCE_TOPICS` and `TOOL_SYSTEM_PROMPT`.
- The functions take the prompt as an argument, so `reference.py` does not import `ai_assistant.py`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_assistant_reference.py`:

```python
"""Reference topics and the tool-mode prompt, both cut from the full prompt."""
import os
import sys
from unittest.mock import MagicMock

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; stub it so the module imports.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1.ai_assistant import REFERENCE_TOPICS, SYSTEM_PROMPT, TOOL_SYSTEM_PROMPT
from app.services.assistant.reference import build_tool_prompt, lookup, split_topics
from app.services.assistant.tools import TOOL_NAMES
from app.services.pipeline_compiler import STEP_TYPE_KEYS

FEATURE_TOPICS = {"artifacts", "runs_on", "notifications", "structure"}


def test_every_step_type_is_a_topic():
    assert set(STEP_TYPE_KEYS) <= set(REFERENCE_TOPICS)


def test_feature_sections_are_topics_and_nothing_else_is():
    assert set(REFERENCE_TOPICS) == set(STEP_TYPE_KEYS) | FEATURE_TOPICS


def test_a_step_topic_is_that_steps_section_only():
    kube = REFERENCE_TOPICS["kube_apply"]
    assert kube.startswith("### kube_apply")
    assert "kubeconfig" in kube
    assert "### http_request" not in kube and "## Artifacts" not in kube


def test_the_last_step_topic_stops_before_the_next_section():
    last = REFERENCE_TOPICS["trigger_pipeline"]
    assert last.startswith("### trigger_pipeline")
    assert "## Artifacts" not in last


def test_feature_topics_hold_their_sections():
    assert REFERENCE_TOPICS["notifications"].startswith("## Notifications")
    assert "on_failure" in REFERENCE_TOPICS["notifications"]
    assert REFERENCE_TOPICS["runs_on"].startswith("## Targeting agents")
    assert REFERENCE_TOPICS["artifacts"].startswith("## Artifacts")
    assert "stages:" in REFERENCE_TOPICS["structure"]


def test_lookup_ignores_case_and_surrounding_space():
    assert lookup(REFERENCE_TOPICS, "  HTTP_Request ") == REFERENCE_TOPICS["http_request"]
    assert lookup(REFERENCE_TOPICS, "teleport") is None


def test_tool_prompt_keeps_the_basics_and_drops_the_step_sections():
    assert TOOL_SYSTEM_PROMPT.startswith("You are MegooCI Pipeline Assistant")
    assert "## Pipeline Structure" in TOOL_SYSTEM_PROMPT
    assert "## Placeholders" in TOOL_SYSTEM_PROMPT
    assert "${{ secrets.NAME }}" in TOOL_SYSTEM_PROMPT
    for name in STEP_TYPE_KEYS:
        assert f"### {name}" not in TOOL_SYSTEM_PROMPT
    assert "## Notifications —" not in TOOL_SYSTEM_PROMPT
    assert len(TOOL_SYSTEM_PROMPT) < len(SYSTEM_PROMPT) / 2


def test_tool_prompt_lists_every_topic_and_names_the_tools_it_tells_the_model_to_use():
    for topic in REFERENCE_TOPICS:
        assert f"`{topic}`" in TOOL_SYSTEM_PROMPT
    for tool in ("read_lines", "search", "replace_text", "replace_lines", "insert_lines",
                 "write_document", "validate", "reference", "list_agents"):
        assert tool in TOOL_NAMES and f"`{tool}`" in TOOL_SYSTEM_PROMPT


def test_tool_prompt_replaces_the_rules_that_ask_for_the_whole_yaml():
    assert "complete, updated pipeline YAML" in SYSTEM_PROMPT
    assert "complete, updated pipeline YAML" not in TOOL_SYSTEM_PROMPT
    assert "Do NOT paste the pipeline YAML into your reply" in TOOL_SYSTEM_PROMPT


def test_the_helpers_work_on_any_prompt_with_the_same_headings():
    prompt = (
        "Intro line.\n\n"
        "## Available Step Types\n\n"
        "### run — Execute\nrun docs\n\n"
        "### notify — Send\nnotify docs\n\n"
        "## Artifacts — Collect\nartifact docs\n\n"
        "## Pipeline Structure\nstructure docs\n\n"
        "## Placeholders\nplaceholder docs\n\n"
        "## Rules\n1. Old rule.\n"
    )
    topics = split_topics(prompt)
    assert list(topics) == ["run", "notify", "artifacts", "structure"]
    assert topics["run"] == "### run — Execute\nrun docs"
    built = build_tool_prompt(prompt, topics)
    assert built.startswith("Intro line.")
    assert "placeholder docs" in built and "Old rule" not in built and "run docs" not in built
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_reference.py
```

Expected: a collection error, `ImportError: cannot import name 'REFERENCE_TOPICS' from 'app.api.v1.ai_assistant'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/assistant/reference.py`:

```python
"""Documentation topics for the ``reference`` tool, and the tool-mode prompt.

The full system prompt stays the single source of the documentation. In tool
mode the prompt is re-sent on every model call, so the per-step and
per-feature sections are cut out of it here and served on demand.
"""

from __future__ import annotations

import re

_STEP_HEADING = re.compile(r"^### (\w+)", re.MULTILINE)

# "## " sections of the full prompt that become topics, by a word in their heading.
_FEATURE_TOPICS = {
    "Artifacts": "artifacts",
    "Targeting agents": "runs_on",
    "Notifications": "notifications",
    "Pipeline Structure": "structure",
}

TOOL_RULES = """\
## How to work
You edit a private working copy of the user's pipeline YAML with tools. Nothing \
reaches the user's editor until they review your changes as a diff and accept them.

1. Look before you edit. The current YAML is shown below when it is short; \
otherwise use `read_lines` and `search`.
2. Make the smallest change that does what was asked. Prefer `replace_text` for \
small edits and `insert_lines` / `replace_lines` for larger ones. Use \
`write_document` only for a new pipeline or a full rewrite. Never touch parts of \
the pipeline the user did not ask about.
3. Before writing a step or feature you have not just read about, call \
`reference` with its topic. Do not guess field names.
4. When you have changed the YAML, call `validate` and fix every problem it \
reports before you finish.
5. Do NOT paste the pipeline YAML into your reply. The user sees your changes as \
a diff. End with a short explanation of what you changed and why.
6. If the user only asked a question, answer it without editing. A short YAML \
snippet in the answer is fine.
7. Always use `${{ secrets.X }}` for sensitive values — never hardcode passwords, \
tokens or webhook URLs. Use the secret and variable names listed under Project \
Context when there are any.
8. For `notify` steps and the `notifications` block, use a configured channel \
name; ask the user which channel if they have not said.
9. Only add `runs_on` when the user asks for a specific OS, architecture or \
agent, or the work is obviously OS-specific. `list_agents` shows what exists.
10. To be told about failed builds, use the top-level `notifications` block — \
never a `notify` step at the end, which does not run after a failure.
"""


def split_topics(prompt: str) -> dict[str, str]:
    """Cut the full prompt into topics: one per step type, plus the feature
    sections. Keys are what the model passes to ``reference``."""
    topics: dict[str, str] = {}

    sections = re.split(r"(?m)^(?=## )", prompt)
    for section in sections:
        heading = section.split("\n", 1)[0]
        if heading.startswith("## Available Step Types"):
            parts = re.split(r"(?m)^(?=### )", section)
            for part in parts[1:]:
                match = _STEP_HEADING.match(part)
                if match:
                    topics[match.group(1)] = part.strip()
            continue
        for word, topic in _FEATURE_TOPICS.items():
            if heading.startswith(f"## {word}"):
                topics[topic] = section.strip()
    return topics


def _section(prompt: str, heading: str) -> str:
    for section in re.split(r"(?m)^(?=## )", prompt):
        if section.startswith(heading):
            return section.strip()
    return ""


def build_tool_prompt(prompt: str, topics: dict[str, str]) -> str:
    """The shorter system prompt used when the model has tools."""
    intro = prompt.split("\n## ", 1)[0].strip()
    index = ", ".join(f"`{name}`" for name in topics)
    return "\n\n".join(
        part
        for part in (
            intro,
            _section(prompt, "## Pipeline Structure"),
            _section(prompt, "## Placeholders"),
            "## Reference topics\n"
            "Call `reference` with one of these topics to read its documentation: "
            f"{index}.",
            TOOL_RULES.strip(),
        )
        if part
    )


def lookup(topics: dict[str, str], topic: str) -> str | None:
    return topics.get(topic.strip().lower())
```

- [ ] **Step 4: Build the topics and the prompt in `ai_assistant.py`**

In `backend/app/api/v1/ai_assistant.py`, add one import after `from app.models.user import User`:

```python
from app.services.assistant.reference import build_tool_prompt, split_topics
```

Then find the end of `SYSTEM_PROMPT` (the closing `"""` after rule 12) and `class ChatMessage(BaseModel):` below it. Put this between them, with one blank line above and two below:

```python
# With tools, the prompt is re-sent on every model call, so the step and
# feature sections are served by the `reference` tool instead of being in it.
REFERENCE_TOPICS = split_topics(SYSTEM_PROMPT)
TOOL_SYSTEM_PROMPT = build_tool_prompt(SYSTEM_PROMPT, REFERENCE_TOPICS)
```

- [ ] **Step 5: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_reference.py tests/test_http_request_docs.py tests/test_failure_notifications_docs.py tests/test_ai_assistant_scoped.py
```

Expected: all pass, 10 of them in `test_assistant_reference.py`. The other three files are the existing tests that read `SYSTEM_PROMPT`; they must pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/assistant/reference.py backend/app/api/v1/ai_assistant.py backend/tests/test_assistant_reference.py
git commit -m "feat(ai): reference topics and tool-mode prompt

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The tools

**Files:**
- Create: `backend/app/services/assistant/tools.py`
- Test: `backend/tests/test_assistant_tools.py` (new)

**Interfaces:**
- Consumes: `WorkingDocument`, `DocumentError` (Task 1); `unified_diff` (Task 2); `lookup` (Task 3); `validate_pipeline_definition` from `app.services.pipeline_compiler`.
- Produces: `TOOL_DEFINITIONS: list[dict]` — ten tools in the function-calling format (`{"type": "function", "function": {"name", "description", "parameters"}}`); `TOOL_NAMES: list[str]`.
- Produces: `ToolOutcome(text: str, label: str, ok: bool = True)` — `text` goes to the model, `label` to the user.
- Produces: `ToolContext(document: WorkingDocument, topics: dict[str, str], list_agents: Callable[[], Awaitable[str]] | None = None)` — `list_agents` is `None` when the user may not see agents.
- Produces: `async run_tool(name: str, arguments: str | None, ctx: ToolContext) -> ToolOutcome` — `arguments` is the JSON text the model produced. Never raises.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_assistant_tools.py`:

```python
"""Tool dispatch: arguments, results for the model, labels for the user."""

import json

import pytest

from app.services.assistant.document import WorkingDocument
from app.services.assistant.tools import (
    TOOL_DEFINITIONS,
    TOOL_NAMES,
    ToolContext,
    run_tool,
)

YAML = (
    "name: demo\n"
    "stages:\n"
    "  - name: build\n"
    "    steps:\n"
    "      - run: make build\n"
)
TOPICS = {"run": "### run — Execute shell commands\n...", "runs_on": "## Targeting agents\n..."}


def ctx(text=YAML, **kwargs):
    return ToolContext(document=WorkingDocument(text), topics=TOPICS, **kwargs)


async def call(context, name, **arguments):
    return await run_tool(name, json.dumps(arguments), context)


def test_the_ten_tools_are_defined_in_the_function_calling_format():
    assert TOOL_NAMES == [
        "read_lines", "search", "replace_text", "replace_lines", "insert_lines",
        "write_document", "validate", "show_diff", "reference", "list_agents",
    ]
    for tool in TOOL_DEFINITIONS:
        assert tool["type"] == "function"
        function = tool["function"]
        assert function["description"]
        assert function["parameters"]["type"] == "object"
        assert set(function["parameters"]["required"]) <= set(function["parameters"]["properties"])


async def test_read_lines_default_and_range():
    c = ctx()
    whole = await call(c, "read_lines")
    assert whole.ok and whole.label == "Read lines 1–5"
    assert whole.text.startswith("1 | name: demo")
    part = await call(c, "read_lines", start=2, end=3)
    assert part.label == "Read lines 2–3" and part.text == "2 | stages:\n3 |   - name: build"


async def test_read_lines_of_an_empty_document():
    out = await call(ctx(""), "read_lines")
    assert out.ok and out.label == "Read the document · empty"


async def test_search_label_counts_matches():
    out = await call(ctx(), "search", pattern="name")
    assert out.label == "Searched “name” · 2 matches"
    one = await call(ctx(), "search", pattern="make")
    assert one.label == "Searched “make” · 1 match"


async def test_replace_text_edits_the_document():
    c = ctx()
    out = await call(c, "replace_text", old="make build", new="make all")
    assert out.ok and out.label == "Replaced text at line 5"
    assert "make all" in c.document.text


async def test_document_errors_come_back_as_failed_results_not_exceptions():
    c = ctx()
    out = await call(c, "replace_text", old="not there", new="x")
    assert out.ok is False
    assert "was not found" in out.text
    assert out.label.startswith("replace_text failed:")
    assert c.document.changed is False


async def test_replace_and_delete_lines_labels():
    c = ctx()
    assert (await call(c, "replace_lines", start=5, end=5, text="      - run: x")).label == "Replaced line 5"
    assert (await call(c, "replace_lines", start=3, end=5, text="")).label == "Deleted lines 3–5"
    assert c.document.line_count == 2


async def test_insert_lines_label():
    c = ctx()
    out = await call(c, "insert_lines", after=5, text="      - run: a\n      - run: b\n")
    assert out.label == "Inserted 2 lines after line 5"
    single = await call(c, "insert_lines", after=0, text="version: 1")
    assert single.label == "Inserted 1 line after line 0"


async def test_write_document_label():
    c = ctx()
    out = await call(c, "write_document", text="name: n\nstages: []\n")
    assert out.label == "Rewrote the document · 2 lines"


async def test_validate_uses_the_real_validator():
    good = await call(ctx(), "validate")
    assert good.text == "No problems." and good.label == "Validated · no problems"

    bad = await call(ctx("name: demo\nstages:\n  - name: s\n    steps:\n      - kube_apply:\n          manifests: [k8s/]\n"), "validate")
    assert bad.ok is True  # the tool ran; the problems are its result
    assert "line 5: " in bad.text and "requires 'kubeconfig'" in bad.text
    assert bad.label == "Validated · 1 problem"


async def test_validate_reports_a_yaml_syntax_error_with_its_line():
    out = await call(ctx("name: demo\nstages:\n  - name: s\n   steps: []\n"), "validate")
    assert "YAML syntax error" in out.text and "line " in out.text


async def test_show_diff_before_and_after_a_change():
    c = ctx()
    assert (await call(c, "show_diff")).text == "No changes yet."
    await call(c, "replace_text", old="make build", new="make all")
    diff = await call(c, "show_diff")
    assert "-      - run: make build" in diff.text and "+      - run: make all" in diff.text


async def test_reference_known_and_unknown_topics():
    c = ctx()
    found = await call(c, "reference", topic=" RUN ")
    assert found.ok and found.text.startswith("### run") and found.label == "Looked up “RUN”"
    missing = await call(c, "reference", topic="teleport")
    assert missing.ok is False
    assert "Topics: run, runs_on" in missing.text


async def test_list_agents_uses_the_provided_lookup():
    async def agents():
        return "linux-1 — linux/amd64, labels: docker, online"

    out = await call(ctx(list_agents=agents), "list_agents")
    assert out.ok and out.text.startswith("linux-1") and out.label == "Listed agents"
    unavailable = await call(ctx(), "list_agents")
    assert unavailable.ok is False


async def test_unknown_tool_lists_the_real_ones():
    out = await run_tool("delete_everything", "{}", ctx())
    assert out.ok is False
    assert "Unknown tool 'delete_everything'" in out.text and "read_lines" in out.text


@pytest.mark.parametrize("arguments", ["{not json", "[1, 2]", '"text"'])
async def test_malformed_arguments(arguments):
    out = await run_tool("read_lines", arguments, ctx())
    assert out.ok is False and "failed: bad arguments" in out.label


@pytest.mark.parametrize("arguments", [None, "", "  "])
async def test_missing_arguments_mean_no_arguments(arguments):
    out = await run_tool("read_lines", arguments, ctx())
    assert out.ok and out.label == "Read lines 1–5"


async def test_wrong_argument_types_and_missing_required_ones():
    c = ctx()
    assert (await call(c, "read_lines", start="two")).ok is False
    assert (await call(c, "read_lines", start=True)).ok is False
    assert (await call(c, "replace_lines", start=1, text="x")).ok is False        # no end
    assert (await call(c, "replace_text", old="a")).ok is False                   # no new
    assert (await call(c, "replace_text", old=5, new="x")).ok is False
    assert (await call(c, "search")).ok is False
    assert c.document.changed is False


async def test_numbers_sent_as_strings_or_floats_are_accepted():
    """Models often send "3" or 3.0 for an integer argument."""
    out = await call(ctx(), "read_lines", start="2", end=3.0)
    assert out.ok and out.label == "Read lines 2–3"


async def test_a_tool_that_blows_up_is_reported_and_does_not_raise(monkeypatch):
    c = ctx()

    def boom(*args, **kwargs):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(c.document, "read_lines", boom)
    out = await call(c, "read_lines")
    assert out.ok is False
    assert out.text == "Internal error while running this tool."
    assert "secret internals" not in out.text + out.label


async def test_long_search_patterns_are_shortened_in_the_label():
    out = await call(ctx(), "search", pattern="x" * 200)
    assert len(out.label) < 70
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_tools.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.services.assistant.tools'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/assistant/tools.py`:

```python
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
        "Find lines containing a pattern. Returns matching lines with their numbers "
        "and one line of context. Plain search ignores case.",
        {"pattern": {**_TEXT, "description": "Text to find, or a regular expression."},
         "regex": {"type": "boolean", "description": "Treat pattern as a regular expression."}},
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
        "Replace a range of lines with new text. Empty text deletes the lines.",
        {"start": {**_LINE, "description": "First line to replace, 1-based."},
         "end": {**_LINE, "description": "Last line to replace, inclusive."},
         "text": {**_TEXT, "description": "The new lines. Empty to delete."}},
        ["start", "end", "text"],
    ),
    _tool(
        "insert_lines",
        "Insert new lines after a line. Use after=0 to insert at the top.",
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


class _BadArguments(Exception):
    pass


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
        text, count = doc.search(pattern, bool(args.get("regex", False)))
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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_tools.py
```

Expected: `25 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/assistant/tools.py backend/tests/test_assistant_tools.py
git commit -m "feat(ai): assistant tools

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The model/tool loop

**Files:**
- Create: `backend/app/services/assistant/loop.py`
- Test: `backend/tests/test_assistant_loop.py` (new)

**Interfaces:**
- Consumes: `ToolContext`, `run_tool` (Task 4).
- Produces: `MAX_TOOL_CALLS = 16`, `MAX_SECONDS = 120.0`.
- Produces: `ToolCall(id: str, name: str, arguments: str | None)`; `ModelTurn(text: str | None, tool_calls: list[ToolCall], message: Any)` — `message` is what gets appended to the conversation before the tool results: the provider's own message object, or a dict; `Step(tool: str, label: str, ok: bool)`; `LoopResult(reply: str, steps: list[Step], limit_reached: bool, model_calls: int)`.
- Produces: type aliases `Complete = Callable[[list[Any]], Awaitable[ModelTurn]]` and `OnStep = Callable[[Step], Awaitable[None]]`.
- Produces: `async run_loop(messages, complete, ctx, *, on_step=None, max_tool_calls=MAX_TOOL_CALLS, max_seconds=MAX_SECONDS, clock=time.monotonic) -> LoopResult`. It extends `messages` in place. An exception from the first `complete` call is raised; a later one ends the loop with `limit_reached=True`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_assistant_loop.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_loop.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.services.assistant.loop'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/assistant/loop.py`:

```python
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
    done so far.
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
        except Exception:
            if result.model_calls == 0:
                raise
            logger.exception("assistant model call failed mid-loop")
            result.limit_reached = True
            break
        result.model_calls += 1

        if not turn.tool_calls:
            result.reply = (turn.text or "").strip()
            break

        messages.append(turn.message)
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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_assistant_loop.py
```

Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/assistant/loop.py backend/tests/test_assistant_loop.py
git commit -m "feat(ai): model and tool loop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The endpoints

**Files:**
- Modify: `backend/app/api/v1/ai_assistant.py` (imports; response models; `_prepare_messages` becomes `_prepare_job`; both endpoints and the code between them)
- Test: `backend/tests/test_ai_assistant_tools.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 1–5; `REFERENCE_TOPICS`, `TOOL_SYSTEM_PROMPT` (Task 3).
- Produces: response models `AssistantStep`, `AssistantProblem`, `AssistantDiffLine`, `AssistantDiffHunk`, `AssistantProposal`, and `AssistantResponse(reply, yaml, mode, limit_reached, steps, proposal)`.
- Produces: `_Job` (model id, AI config, the two conversations, the editor's YAML, the `list_agents` callable or `None`) and `async _prepare_job(body, db, current_user) -> _Job`, which replaces `_prepare_messages`.
- Produces: `async _answer(job: _Job, on_step: OnStep | None = None) -> AssistantResponse` — raises the provider's exception when no answer could be had.
- Produces: `_provider_error_detail(exc) -> str`, `_stream_events(job)`, `KEEPALIVE_SECONDS = 15.0`, `MAX_AGENTS_LISTED = 50`.
- `POST /ai/assistant` returns `AssistantResponse`. `POST /ai/assistant/stream` sends `data: <json>` events with `type` `step`, `done` or `error`, and `: keep-alive` comment lines.
- Unchanged: `_check_ai_access`, `_build_project_context`, `_build_repo_context`, `_extract_yaml`, `AssistantRequest`, `ChatMessage`, `SYSTEM_PROMPT`.

**How a request is answered (`_answer`):**

1. Run the loop with the tool conversation. If the first model call raises one of the four errors a retry cannot fix, re-raise. If it raises anything else, make one call with the legacy conversation and no tools; `mode` becomes `"legacy"`.
2. If the working copy is unchanged and the reply contains a whole pipeline (a YAML block with a top-level `stages:`), write it to the working copy and remove the block from the reply.
3. If the working copy changed, validate it and build the proposal.
4. An empty reply is replaced with a fixed sentence that fits the outcome.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_ai_assistant_tools.py`:

```python
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
    assert response.reply == "Switched to make all."


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
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_ai_assistant_tools.py
```

Expected: no test passes. The tests that use the `sf` fixture error with `AttributeError: module 'app.api.v1.ai_assistant' has no attribute 'database'`.

- [ ] **Step 3: Replace the imports**

In `backend/app/api/v1/ai_assistant.py`, replace everything from `from __future__ import annotations` through the line `from app.services.assistant.reference import build_tool_prompt, split_topics` (added in Task 3) with:

```python
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("uvicorn.error")

import litellm
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.api.v1.agents import _normalize_status
from app.config import get_settings
from app.core.access import has_global_permission, project_id_for_pipeline
from app.core.deps import (
    check_scoped_permission,
    effective_scoped_permissions,
    get_current_active_user,
)
from app.database import get_db
from app.models.agent import Agent
from app.models.git_integration import ProjectRepository
from app.models.pipeline import Pipeline
from app.models.secret import EnvVar, Secret
from app.models.user import User
from app.services.assistant.diff import build_proposal
from app.services.assistant.document import DocumentError, WorkingDocument
from app.services.assistant.loop import LoopResult, ModelTurn, OnStep, Step, ToolCall, run_loop
from app.services.assistant.reference import build_tool_prompt, split_topics
from app.services.assistant.tools import TOOL_DEFINITIONS, ToolContext
from app.services.pipeline_compiler import validate_pipeline_definition
```

The module docstring above it, and `litellm.drop_params = True` and everything else below it, stay as they are.

- [ ] **Step 4: Replace the response model**

Replace the three-line class

```python
class AssistantResponse(BaseModel):
    reply: str
    yaml: str | None = None
```

with:

```python
class AssistantStep(BaseModel):
    """One tool call the assistant made, as shown in the chat."""

    tool: str
    label: str
    ok: bool = True


class AssistantProblem(BaseModel):
    message: str
    line: int | None = None


class AssistantDiffLine(BaseModel):
    kind: str  # "context" | "add" | "remove"
    old: int | None = None
    new: int | None = None
    text: str


class AssistantDiffHunk(BaseModel):
    old_start: int
    new_start: int
    lines: list[AssistantDiffLine]


class AssistantProposal(BaseModel):
    """The YAML the assistant proposes, and how it differs from the editor's."""

    yaml: str
    added: int
    removed: int
    problems: list[AssistantProblem] = []
    hunks: list[AssistantDiffHunk] = []


class AssistantResponse(BaseModel):
    reply: str
    # Same as proposal.yaml; kept for clients that predate the proposal.
    yaml: str | None = None
    mode: str = "tools"  # "tools" | "legacy"
    limit_reached: bool = False
    steps: list[AssistantStep] = []
    proposal: AssistantProposal | None = None
```

- [ ] **Step 5: Replace `_prepare_messages` with `_prepare_job`**

Replace the whole function `_prepare_messages` — from `async def _prepare_messages(` to the blank lines before `async def _check_ai_access(` — with the code below. The AI-configuration checks, the project context and the repository context are the same code as before; what changes is that the context is appended to two system prompts and the function returns a `_Job`.

```python
MAX_AGENTS_LISTED = 50


@dataclass
class _Job:
    """Everything one assistant request needs, gathered while the request's
    database session is still open."""

    model_id: str
    ai_cfg: dict
    tool_messages: list[Any]
    legacy_messages: list[Any]
    current_yaml: str | None
    # None when the user may not see agents.
    list_agents: Callable[[], Awaitable[str]] | None


def _history(body: AssistantRequest) -> list[dict[str, str]]:
    return [
        {"role": msg.role, "content": msg.content}
        for msg in body.history or []
        if msg.role in ("user", "assistant")
    ]


def _legacy_messages(body: AssistantRequest, system_content: str) -> list[Any]:
    """The conversation for a model called without tools: it gets the whole
    YAML and must return the whole YAML."""
    messages: list[Any] = [{"role": "system", "content": system_content}, *_history(body)]

    if body.current_yaml:
        messages.append({
            "role": "user",
            "content": (
                "Here is my current pipeline YAML from the editor "
                "(this reflects the latest state, including any manual edits I made).\n"
                "IMPORTANT: When I ask you to modify, fix, or update this pipeline, "
                "you MUST return the COMPLETE updated pipeline YAML inside a "
                "```yaml code block — not just the changed part. My editor replaces "
                "the entire pipeline with your output.\n\n"
                f"```yaml\n{body.current_yaml}\n```"
            ),
        })
        messages.append({
            "role": "assistant",
            "content": (
                "Got it — I can see your full pipeline YAML. "
                "When you ask me to make changes, I'll always return the "
                "complete updated pipeline in a ```yaml block so you can "
                "apply it directly. What would you like me to do?"
            ),
        })

    messages.append({"role": "user", "content": body.prompt})
    return messages


def _tool_messages(body: AssistantRequest, system_content: str) -> list[Any]:
    """The conversation for a model called with tools. The YAML is shown with
    line numbers so the model can edit short pipelines without reading first."""
    document = WorkingDocument(body.current_yaml)
    if document.line_count:
        editor = (
            f"The pipeline YAML in my editor right now ({document.line_count} lines, "
            f"each prefixed with its line number):\n{document.read_lines()}"
        )
    else:
        editor = "My editor is empty: there is no pipeline YAML yet."
    return [
        {"role": "system", "content": system_content},
        *_history(body),
        {"role": "user", "content": f"{editor}\n\nMy request: {body.prompt}"},
    ]


def _agent_lister() -> Callable[[], Awaitable[str]]:
    """The ``list_agents`` tool. It opens its own session because the
    request's session may be closed by the time a streamed reply calls it."""

    async def list_agents() -> str:
        async with database.async_session() as session:
            result = await session.execute(
                select(Agent).order_by(Agent.name).limit(MAX_AGENTS_LISTED)
            )
            agents = list(result.scalars().all())
            # Detached, so working out who is offline below cannot be saved.
            session.expunge_all()
        if not agents:
            return "No agents are registered."
        lines = []
        for agent in agents:
            _normalize_status(agent)
            state = agent.status if agent.enabled else "disabled"
            labels = ", ".join(str(label) for label in agent.labels or []) or "none"
            lines.append(
                f"- {agent.name}: os={agent.os or 'unknown'}, "
                f"arch={agent.arch or 'unknown'}, labels={labels}, {state}"
            )
        return "\n".join(lines)

    return list_agents


async def _prepare_job(
    body: AssistantRequest,
    db: AsyncSession,
    current_user: User,
) -> _Job:
    """Shared by both assistant endpoints: check the AI configuration and
    build the conversations for a request."""
    from app.api.v1.system import get_ai_overrides, resolve_ai_config

    overrides = await get_ai_overrides(db)
    ai_cfg = resolve_ai_config(overrides)

    logger.info(
        "AI assistant request — provider=%s model=%s reasoning_model=%s "
        "base_url=%s enabled=%s has_key=%s",
        ai_cfg["provider"],
        ai_cfg["model"],
        ai_cfg.get("reasoning_model"),
        ai_cfg["base_url"] or "(default)",
        ai_cfg["enabled"],
        bool(ai_cfg["api_key"]),
    )

    if not ai_cfg["enabled"]:
        logger.warning("AI assistant is disabled, returning 503")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is disabled",
        )
    if not ai_cfg["api_key"] and ai_cfg["provider"] in ("openai", "anthropic", "azure_openai"):
        logger.warning("AI API key missing for provider=%s", ai_cfg["provider"])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI API key is not configured",
        )

    # Appended to the system prompt in both modes.
    context = ""

    if body.project_id:
        # Resolve project_id for scoped secrets.read check.
        try:
            _pid_for_secrets = uuid.UUID(body.project_id)
        except ValueError:
            _pid_for_secrets = None
        can_read_secrets = (
            _pid_for_secrets is not None
            and (
                current_user.is_admin
                or "secrets.read" in effective_scoped_permissions(current_user, "project", _pid_for_secrets)
            )
        )
        project_ctx = await _build_project_context(
            db, body.project_id, include_values=can_read_secrets,
        )
        if project_ctx:
            context += (
                "\n\n## Project Context\n"
                "The user's project has the following secrets and variables "
                "configured. Use these exact names in the generated YAML "
                "instead of generic placeholders.\n\n"
                + project_ctx
            )

    # ----- Repository & Branch context -----
    repo_ctx = await _build_repo_context(
        db,
        repo_url=body.repo_url,
        branch=body.branch,
        pipeline_id=body.pipeline_id,
    )
    if repo_ctx:
        context += repo_ctx

    model_id = _build_model_id(ai_cfg)

    logger.info(
        "Sending AI request — model_id=%s provider=%s history=%d base_url=%s",
        model_id,
        ai_cfg["provider"],
        len(body.history or []),
        ai_cfg["base_url"] or "(default)",
    )

    return _Job(
        model_id=model_id,
        ai_cfg=ai_cfg,
        tool_messages=_tool_messages(body, TOOL_SYSTEM_PROMPT + context),
        legacy_messages=_legacy_messages(body, SYSTEM_PROMPT + context),
        current_yaml=body.current_yaml,
        list_agents=_agent_lister() if has_global_permission(current_user, "agents.read") else None,
    )
```

- [ ] **Step 6: Replace the endpoints**

Replace everything from `@router.post("/assistant", response_model=AssistantResponse)` to the blank lines before `def _extract_yaml(` — that is `pipeline_assistant`, `_stream_generator` and `pipeline_assistant_stream` — with the code below. Leave `_extract_yaml` as it is.

```python
def _model_options(job: _Job) -> dict[str, Any]:
    ai_cfg = job.ai_cfg
    return {
        "model": job.model_id,
        "temperature": 0.3,
        "timeout": 120,
        "api_key": str(ai_cfg["api_key"]) if ai_cfg["api_key"] else None,
        "api_base": str(ai_cfg["base_url"]) if ai_cfg["base_url"] else None,
    }


def _count_tokens(response: Any, usage: dict[str, int]) -> None:
    total = getattr(getattr(response, "usage", None), "total_tokens", None)
    if isinstance(total, int):
        usage["tokens"] += total


def _tool_completer(job: _Job, usage: dict[str, int]) -> Callable[[list[Any]], Awaitable[ModelTurn]]:
    """One model call with the tools attached, as the loop wants it."""

    async def complete(messages: list[Any]) -> ModelTurn:
        response = await litellm.acompletion(
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            **_model_options(job),
        )
        _count_tokens(response, usage)
        message = response.choices[0].message
        calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
            for call in getattr(message, "tool_calls", None) or []
        ]
        # The provider's own message object goes back into the conversation
        # unchanged, so anything it needs to see again (ids, reasoning) is kept.
        return ModelTurn(text=message.content, tool_calls=calls, message=message)

    return complete


async def _legacy_reply(job: _Job, usage: dict[str, int]) -> str:
    """One model call without tools; the YAML comes back inside the reply."""
    response = await litellm.acompletion(messages=job.legacy_messages, **_model_options(job))
    _count_tokens(response, usage)
    return (response.choices[0].message.content or "").strip()


_YAML_BLOCK = re.compile(r"```(?:ya?ml)?\s*\n.*?```", re.DOTALL)


def _is_whole_pipeline(text: str) -> bool:
    """True for a full pipeline, false for a snippet shown in an answer: only
    a full pipeline may replace the editor's content."""
    return re.search(r"(?m)^stages\s*:", text) is not None


def _without_yaml_block(reply: str, yaml_text: str) -> str:
    """The reply with its YAML removed, once that YAML is shown as a diff."""
    rest = _YAML_BLOCK.sub("", reply, count=1).strip()
    return "" if rest == yaml_text else rest


def _default_reply(changed: bool, limit_reached: bool) -> str:
    if limit_reached and changed:
        return (
            "I ran out of steps before finishing. The changes I made so far are "
            "below — check them before applying."
        )
    if limit_reached:
        return "I ran out of steps before making a change. Try a more specific request."
    if changed:
        return "I updated the pipeline. Review the changes below."
    return "I could not produce an answer. Please try again."


async def _answer(job: _Job, on_step: OnStep | None = None) -> AssistantResponse:
    """Answer one request: with tools when the model accepts them, otherwise
    with a single call whose reply carries the YAML.

    Raises whatever the AI provider raised when no answer could be had.
    """
    document = WorkingDocument(job.current_yaml)
    ctx = ToolContext(document=document, topics=REFERENCE_TOPICS, list_agents=job.list_agents)
    usage = {"tokens": 0}
    mode = "tools"
    result = LoopResult()

    try:
        result = await run_loop(job.tool_messages, _tool_completer(job, usage), ctx, on_step=on_step)
        reply = result.reply
    except (
        litellm.exceptions.AuthenticationError,
        litellm.exceptions.APIConnectionError,
        litellm.exceptions.Timeout,
        litellm.exceptions.RateLimitError,
    ):
        # A call without tools would fail the same way.
        raise
    except Exception as exc:
        # Most often a model or endpoint that does not accept tools.
        logger.warning(
            "AI call with tools failed (%s: %s) — retrying without tools",
            type(exc).__name__, exc,
        )
        mode = "legacy"
        reply = await _legacy_reply(job, usage)

    if not document.changed:
        # Without tools — or with a model that ignored them — the whole
        # pipeline is in the reply. Turn it into the same proposal.
        candidate = _extract_yaml(reply)
        if candidate and _is_whole_pipeline(candidate):
            try:
                document.write(candidate)
            except DocumentError as exc:
                logger.warning("AI reply YAML not usable as a proposal — %s", exc)
            else:
                reply = _without_yaml_block(reply, candidate)

    proposal = None
    if document.changed:
        problems = [
            {"message": problem.message, "line": problem.line}
            for problem in validate_pipeline_definition(document.text)
        ]
        built = build_proposal(document.original, document.text, problems)
        if built is not None:
            proposal = AssistantProposal.model_validate(built.to_dict())

    logger.info(
        "AI assistant response — mode=%s model_calls=%d tool_calls=%d tokens=%d "
        "limit_reached=%s proposal=%s",
        mode, result.model_calls, len(result.steps), usage["tokens"],
        result.limit_reached, proposal is not None,
    )

    return AssistantResponse(
        reply=reply or _default_reply(proposal is not None, result.limit_reached),
        yaml=proposal.yaml if proposal else None,
        mode=mode,
        limit_reached=result.limit_reached,
        steps=[AssistantStep(tool=s.tool, label=s.label, ok=s.ok) for s in result.steps],
        proposal=proposal,
    )


def _provider_error_detail(exc: Exception) -> str:
    """Log a failed assistant request and describe it for the user."""
    if isinstance(exc, litellm.exceptions.AuthenticationError):
        logger.error("AI provider authentication failed — %s", exc)
        return f"AI provider authentication failed: {exc.message}"
    if isinstance(exc, litellm.exceptions.BadRequestError):
        logger.error("AI provider bad request — %s", exc)
        return f"AI provider rejected request: {exc.message}"
    if isinstance(exc, litellm.exceptions.APIConnectionError):
        logger.error("AI provider unreachable — %s", exc)
        return f"AI provider unreachable: {exc.message}"
    if isinstance(exc, (AttributeError, IndexError, TypeError)):
        logger.error("Failed to parse AI response — %s", exc)
        return f"Unexpected AI provider response format: {exc}"
    logger.error("AI provider error — %s: %s", type(exc).__name__, exc)
    return f"AI provider error: {exc}"


@router.post("/assistant", response_model=AssistantResponse)
async def pipeline_assistant(
    body: AssistantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AssistantResponse:
    await _check_ai_access(body, db, current_user)
    job = await _prepare_job(body, db, current_user)

    try:
        return await _answer(job)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_provider_error_detail(exc),
        )


# Proxies close a connection that stays silent; a long model call is silent.
KEEPALIVE_SECONDS = 15.0


def _event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_events(job: _Job) -> AsyncIterator[str]:
    """Server-sent events for one request: a ``step`` after each tool call,
    then one ``done`` (the response) or one ``error``."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_step(step: Step) -> None:
        await queue.put(_event({"type": "step", "tool": step.tool, "label": step.label, "ok": step.ok}))

    async def work() -> None:
        try:
            response = await _answer(job, on_step)
            await queue.put(_event({"type": "done", **response.model_dump()}))
        except Exception as exc:
            await queue.put(_event({"type": "error", "detail": _provider_error_detail(exc)}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    waiting = asyncio.ensure_future(queue.get())
    try:
        while True:
            done, _ = await asyncio.wait({waiting}, timeout=KEEPALIVE_SECONDS)
            if not done:
                yield ": keep-alive\n\n"
                continue
            item = waiting.result()
            if item is None:
                break
            yield item
            waiting = asyncio.ensure_future(queue.get())
    finally:
        # Reached early when the client goes away: stop paying for the model.
        waiting.cancel()
        task.cancel()


@router.post("/assistant/stream")
async def pipeline_assistant_stream(
    body: AssistantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _check_ai_access(body, db, current_user)
    job = await _prepare_job(body, db, current_user)
    return StreamingResponse(
        _stream_events(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 7: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_ai_assistant_tools.py
```

Expected: `34 passed`.

- [ ] **Step 8: Run the whole backend suite**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `557 passed`.

- [ ] **Step 9: Commit**

```bash
git add backend/app/api/v1/ai_assistant.py backend/tests/test_ai_assistant_tools.py
git commit -m "feat(ai): assistant endpoints use tools and return a proposal

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Frontend types and the streaming call

**Files:**
- Modify: `frontend/src/lib/api.ts` (the "AI Pipeline Assistant" section)

**Interfaces:**
- Consumes: the response and the stream events of Task 6.
- Produces: exported types `AiAssistantStep`, `AiDiffLine`, `AiDiffHunk`, `AiProposalProblem`, `AiProposal`; `AiAssistantResponse` gains `mode`, `limit_reached`, `steps`, `proposal`.
- Produces: `aiAssistantApi.stream(data, { onStep?, signal? }): Promise<AiAssistantResponse>`. It rejects with an `ApiError` for an HTTP error, an `error` event, or a stream that ends without `done`. An aborted request also rejects; the caller knows it aborted.
- `aiAssistantApi.ask` stays.

- [ ] **Step 1: Replace the response type and the API object**

In `frontend/src/lib/api.ts`, in the "AI Pipeline Assistant" section, keep `AiChatMessage` and `AiAssistantRequest`. Replace the `AiAssistantResponse` interface and the whole `aiAssistantApi` object (everything from `export interface AiAssistantResponse {` to the `};` that closes `aiAssistantApi`) with:

```ts
export interface AiAssistantStep {
  tool: string;
  label: string;
  ok: boolean;
}

export interface AiDiffLine {
  kind: "context" | "add" | "remove";
  /** Line number before the change; null for an added line. */
  old: number | null;
  /** Line number after the change; null for a removed line. */
  new: number | null;
  text: string;
}

export interface AiDiffHunk {
  old_start: number;
  new_start: number;
  lines: AiDiffLine[];
}

export interface AiProposalProblem {
  message: string;
  line: number | null;
}

export interface AiProposal {
  yaml: string;
  added: number;
  removed: number;
  problems: AiProposalProblem[];
  hunks: AiDiffHunk[];
}

export interface AiAssistantResponse {
  reply: string;
  /** Same as proposal.yaml, or null when nothing is proposed. */
  yaml: string | null;
  mode: "tools" | "legacy";
  limit_reached: boolean;
  steps: AiAssistantStep[];
  proposal: AiProposal | null;
}

type AiAssistantStreamEvent =
  | ({ type: "step" } & AiAssistantStep)
  | ({ type: "done" } & AiAssistantResponse)
  | { type: "error"; detail: string };

export const aiAssistantApi = {
  ask: (data: AiAssistantRequest) => {
    // Reasoning models can take 60–120s to respond; use a generous timeout
    // so the request isn't killed by the browser's default.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120_000);
    return fetchApi<AiAssistantResponse>("/api/v1/ai/assistant", {
      method: "POST",
      body: JSON.stringify(data),
      signal: controller.signal,
    }).finally(() => clearTimeout(timer));
  },

  /**
   * Ask over the streaming endpoint. `onStep` is called after each tool the
   * assistant uses; the promise resolves with the final response. Abort with
   * `signal` to stop the request.
   */
  stream: async (
    data: AiAssistantRequest,
    options: {
      onStep?: (step: AiAssistantStep) => void;
      signal?: AbortSignal;
    } = {},
  ): Promise<AiAssistantResponse> => {
    const endpoint = "/api/v1/ai/assistant/stream";
    const init: RequestInit = {
      method: "POST",
      body: JSON.stringify(data),
      signal: options.signal,
    };

    let res = await performFetch(endpoint, init, getAccessToken());
    if (res.status === 401) {
      const newAccess = await refreshAccessTokenOnce();
      if (newAccess) res = await performFetch(endpoint, init, newAccess);
    }
    if (!res.ok) {
      let body: unknown;
      try {
        body = await res.json();
      } catch {
        body = await res.text();
      }
      throw new ApiError(res.status, body, extractErrorMessage(res.status, body));
    }
    if (!res.body) {
      throw new ApiError(0, null, "The server sent an empty response.");
    }

    // Server-sent events: frames separated by a blank line, each a
    // `data: <json>` line. Lines starting with ":" only keep the connection
    // open while the model is thinking.
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end = buffer.indexOf("\n\n");
      while (end !== -1) {
        const frame = buffer.slice(0, end);
        buffer = buffer.slice(end + 2);
        end = buffer.indexOf("\n\n");
        for (const line of frame.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let event: AiAssistantStreamEvent;
          try {
            event = JSON.parse(line.slice(6));
          } catch {
            continue;
          }
          if (event.type === "step") {
            options.onStep?.({ tool: event.tool, label: event.label, ok: event.ok });
          } else if (event.type === "done") {
            return event;
          } else if (event.type === "error") {
            throw new ApiError(502, event, event.detail);
          }
        }
      }
    }
    throw new ApiError(
      0,
      null,
      "The connection closed before the assistant finished. Please try again.",
    );
  },
};
```

`performFetch`, `getAccessToken`, `refreshAccessTokenOnce`, `extractErrorMessage` and `ApiError` are already defined at the top of the file.

- [ ] **Step 2: Type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0. The panel still compiles because it reads only `reply` and `yaml`.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(ai): streaming assistant client and proposal types

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The review card and the panel

**Files:**
- Create: `frontend/src/components/pipeline/ai-proposal-card.tsx`
- Modify: `frontend/src/components/pipeline/ai-assistant-panel.tsx` (whole file)

**Interfaces:**
- Consumes: `aiAssistantApi.stream` and the types of Task 7.
- Produces: `AiProposalCard` with props `proposal`, `decision?: "applied" | "discarded"`, `stale`, `limitReached`, `canUndo`, `onApply?`, `onDiscard`, `onUndo`; and the type `ProposalDecision`.
- `AiAssistantPanel` keeps its props. The two pages that render it (`frontend/src/app/pipelines/new/page.tsx`, `frontend/src/app/pipelines/[id]/page.tsx`) do not change.

**Behaviour to keep in mind:**
- The panel stays mounted while its sheet is closed (`components/ui/sheet.tsx`), so a request keeps running and its answer is there when the sheet is reopened. Clearing the chat or leaving the page aborts the request.
- A proposal is **stale** when the editor's content differs from the content the request was sent with. Applying one proposal therefore makes every other pending card stale.
- **Undo** is offered for the last applied proposal while the editor still holds exactly what was applied. It restores the content from before Apply and returns the card to pending.
- On the pipeline page outside edit mode, `onApplyYaml` is undefined: the card shows the diff and Copy YAML, and says to edit the pipeline to apply.

- [ ] **Step 1: Create the review card**

Create `frontend/src/components/pipeline/ai-proposal-card.tsx`:

```tsx
"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import type { AiDiffHunk, AiDiffLine, AiProposal } from "@/lib/api";
import {
  AlertTriangle,
  ArrowDownToLine,
  Check,
  Copy,
  Undo2,
} from "lucide-react";
import { Button } from "@/components/ui/button";

/** Longer diffs are cut. Apply and Copy still use the whole proposal. */
const MAX_DIFF_LINES = 400;

export type ProposalDecision = "applied" | "discarded";

interface AiProposalCardProps {
  proposal: AiProposal;
  /** Undefined until the user applies or discards the proposal. */
  decision?: ProposalDecision;
  /** The editor no longer holds the YAML this proposal was made from. */
  stale: boolean;
  /** The assistant stopped at a limit, so the proposal may be incomplete. */
  limitReached: boolean;
  canUndo: boolean;
  /** Undefined when the editor cannot be changed (not in edit mode). */
  onApply?: () => void;
  onDiscard: () => void;
  onUndo: () => void;
}

function hunkTitle(hunk: AiDiffHunk): string {
  const numbers = hunk.lines
    .map((line) => line.new)
    .filter((n): n is number => n !== null);
  if (numbers.length === 0) return `removed at line ${hunk.old_start}`;
  const first = numbers[0];
  const last = numbers[numbers.length - 1];
  return first === last ? `line ${first}` : `lines ${first}–${last}`;
}

function cutHunks(hunks: AiDiffHunk[]): { shown: AiDiffHunk[]; hidden: number } {
  const shown: AiDiffHunk[] = [];
  let room = MAX_DIFF_LINES;
  let hidden = 0;
  for (const hunk of hunks) {
    if (room === 0) {
      hidden += hunk.lines.length;
    } else if (hunk.lines.length <= room) {
      shown.push(hunk);
      room -= hunk.lines.length;
    } else {
      shown.push({ ...hunk, lines: hunk.lines.slice(0, room) });
      hidden += hunk.lines.length - room;
      room = 0;
    }
  }
  return { shown, hidden };
}

function Counts({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="font-mono">
      <span className="text-success">+{added}</span>{" "}
      <span className="text-destructive">−{removed}</span>
    </span>
  );
}

function DiffRow({ line }: { line: AiDiffLine }) {
  const isAdd = line.kind === "add";
  const isRemove = line.kind === "remove";
  return (
    <div
      className={cn(
        "flex",
        isAdd && "bg-success/10",
        isRemove && "bg-destructive/10",
      )}
    >
      <span className="w-10 shrink-0 select-none pr-2 text-right text-muted-foreground/70">
        {isRemove ? line.old : line.new}
      </span>
      <span
        className={cn(
          "w-4 shrink-0 select-none",
          isAdd && "text-success",
          isRemove && "text-destructive",
        )}
      >
        {isAdd ? "+" : isRemove ? "−" : ""}
      </span>
      <span className="whitespace-pre pr-3">{line.text}</span>
    </div>
  );
}

/**
 * The assistant's proposed change, shown as a diff the user applies or
 * discards. Nothing reaches the editor until Apply.
 */
export function AiProposalCard({
  proposal,
  decision,
  stale,
  limitReached,
  canUndo,
  onApply,
  onDiscard,
  onUndo,
}: AiProposalCardProps) {
  const [copied, setCopied] = React.useState(false);
  const { shown, hidden } = React.useMemo(
    () => cutHunks(proposal.hunks),
    [proposal.hunks],
  );

  if (decision === "applied") {
    return (
      <div className="my-2 flex items-center justify-between gap-2 rounded-md border bg-muted/30 px-3 py-1.5 text-xs">
        <span className="flex items-center gap-1.5">
          <Check className="h-3 w-3 text-success" />
          Applied to editor ·{" "}
          <Counts added={proposal.added} removed={proposal.removed} />
        </span>
        {canUndo && (
          <button
            type="button"
            onClick={onUndo}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <Undo2 className="h-3 w-3" />
            Undo
          </button>
        )}
      </div>
    );
  }

  if (decision === "discarded") {
    return (
      <div className="my-2 rounded-md border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
        Discarded ·{" "}
        <Counts added={proposal.added} removed={proposal.removed} />
      </div>
    );
  }

  const problems = proposal.problems;

  return (
    <div className="my-2 overflow-hidden rounded-md border bg-muted/30">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b bg-muted/50 px-3 py-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          Proposed change
        </span>
        <div className="flex items-center gap-2.5 text-xs">
          <Counts added={proposal.added} removed={proposal.removed} />
          {problems.length === 0 ? (
            <span className="flex items-center gap-1 text-success">
              <Check className="h-3 w-3" />
              valid
            </span>
          ) : (
            <span className="flex items-center gap-1 text-warning">
              <AlertTriangle className="h-3 w-3" />
              {problems.length} {problems.length === 1 ? "problem" : "problems"}
            </span>
          )}
          <button
            type="button"
            onClick={() => {
              navigator.clipboard.writeText(proposal.yaml);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
            {copied ? "Copied" : "Copy YAML"}
          </button>
        </div>
      </div>

      {(limitReached || stale || problems.length > 0) && (
        <div className="space-y-1 border-b px-3 py-2 text-xs">
          {limitReached && (
            <p className="text-warning">
              The assistant stopped early, so this change may be incomplete.
            </p>
          )}
          {stale && (
            <p className="text-warning">
              The editor changed since this was proposed. Applying replaces what
              is in the editor now.
            </p>
          )}
          {problems.map((problem, i) => (
            <p key={i} className="text-muted-foreground">
              {problem.line !== null && (
                <span className="font-mono">Line {problem.line}: </span>
              )}
              {problem.message}
            </p>
          ))}
        </div>
      )}

      <div className="max-h-80 overflow-auto font-mono text-xs leading-relaxed">
        <div className="w-max min-w-full">
          {shown.map((hunk, i) => (
            <React.Fragment key={i}>
              <div className="border-b bg-muted/40 px-3 py-0.5 text-[10px] text-muted-foreground">
                {hunkTitle(hunk)}
              </div>
              {hunk.lines.map((line, j) => (
                <DiffRow key={j} line={line} />
              ))}
            </React.Fragment>
          ))}
          {hidden > 0 && (
            <div className="border-t px-3 py-1 font-sans text-[11px] text-muted-foreground">
              {hidden} more {hidden === 1 ? "line is" : "lines are"} not shown.
              Copy YAML to see the whole result.
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 border-t px-3 py-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDiscard}>
          Discard
        </Button>
        {onApply ? (
          <Button type="button" size="sm" onClick={onApply}>
            <ArrowDownToLine className="mr-1.5 h-3.5 w-3.5" />
            {stale ? "Apply anyway" : "Apply to editor"}
          </Button>
        ) : (
          <span className="text-xs text-muted-foreground">
            Edit the pipeline to apply this change.
          </span>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Replace the panel**

Replace the whole content of `frontend/src/components/pipeline/ai-assistant-panel.tsx` with:

```tsx
"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import {
  aiAssistantApi,
  type AiAssistantStep,
  type AiChatMessage,
  type AiProposal,
} from "@/lib/api";
import { toast } from "sonner";
import {
  Sparkles,
  Send,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  User,
  Bot,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  AiProposalCard,
  type ProposalDecision,
} from "@/components/pipeline/ai-proposal-card";

/**
 * Longer than the server needs for its tool loop (120s) plus one retry
 * without tools, so the server's own answer normally arrives first.
 */
const REQUEST_TIMEOUT_MS = 250_000;

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Tools the assistant used for this reply. */
  steps?: AiAssistantStep[];
  proposal?: AiProposal | null;
  limitReached?: boolean;
  /** Editor content when the request was sent; the proposal is a diff against it. */
  baseYaml?: string;
  decision?: ProposalDecision;
}

/** The last applied proposal, while it can still be undone. */
interface UndoState {
  messageId: string;
  before: string;
  after: string;
}

interface AiAssistantPanelProps {
  className?: string;
  currentYaml: string;
  onApplyYaml?: (yaml: string) => void;
  projectId?: string | null;
  pipelineId?: string | null;
  repoUrl?: string | null;
  branch?: string | null;
  /** Called when the user clicks the close button in the header. */
  onClose?: () => void;
}

const QUICK_PROMPTS = [
  "Build and push a Docker image",
  "Deploy via SSH after approval",
  "Full CI/CD with test, build, deploy",
  "Add a webhook gate before deploy",
  "Clone repo, install, test, and push",
];

function StepRows({ steps }: { steps: AiAssistantStep[] }) {
  return (
    <ul className="space-y-0.5 text-xs text-muted-foreground">
      {steps.map((step, i) => (
        <li key={i} className="flex items-start gap-1.5">
          {step.ok ? (
            <Check className="mt-0.5 h-3 w-3 shrink-0 text-success" />
          ) : (
            <X className="mt-0.5 h-3 w-3 shrink-0 text-destructive" />
          )}
          <span className="min-w-0 break-words">{step.label}</span>
        </li>
      ))}
    </ul>
  );
}

/** The steps of a finished reply: one line that expands to the list. */
function StepSummary({ steps }: { steps: AiAssistantStep[] }) {
  const [open, setOpen] = React.useState(false);
  if (steps.length === 0) return null;
  return (
    <div className="mb-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        {steps.length} {steps.length === 1 ? "step" : "steps"}
      </button>
      {open && (
        <div className="mt-1 pl-4">
          <StepRows steps={steps} />
        </div>
      )}
    </div>
  );
}

/** Reply text, with ``` fenced blocks shown as code. */
function ReplyText({ text }: { text: string }) {
  const parts = text.split(/```[\w-]*\n?([\s\S]*?)```/g);
  return (
    <div className="text-sm leading-relaxed">
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <pre
            key={i}
            className="my-2 overflow-x-auto rounded-md border bg-muted/30 p-3 text-xs leading-relaxed"
          >
            <code>{part.replace(/\n$/, "")}</code>
          </pre>
        ) : part.trim() ? (
          <div key={i} className="whitespace-pre-wrap">
            {part.trim()}
          </div>
        ) : null,
      )}
    </div>
  );
}

export function AiAssistantPanel({
  className,
  currentYaml,
  onApplyYaml,
  projectId,
  pipelineId,
  repoUrl,
  branch,
  onClose,
}: AiAssistantPanelProps) {
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [input, setInput] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [liveSteps, setLiveSteps] = React.useState<AiAssistantStep[]>([]);
  const [undo, setUndo] = React.useState<UndoState | null>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const yamlRef = React.useRef(currentYaml);
  yamlRef.current = currentYaml;
  /** The request in flight, if any. */
  const requestRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, liveSteps]);

  // Leaving the page stops the request.
  React.useEffect(() => {
    return () => {
      requestRef.current?.abort();
      requestRef.current = null;
    };
  }, []);

  async function sendMessage(prompt: string) {
    if (!prompt.trim() || loading) return;

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: prompt.trim(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setLiveSteps([]);

    const latestYaml = yamlRef.current;
    const received: AiAssistantStep[] = [];
    const controller = new AbortController();
    requestRef.current = controller;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);

    try {
      const history: AiChatMessage[] = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const resp = await aiAssistantApi.stream(
        {
          prompt: prompt.trim(),
          current_yaml: latestYaml || null,
          project_id: projectId || null,
          pipeline_id: pipelineId || null,
          repo_url: repoUrl || null,
          branch: branch || null,
          history: history.length > 0 ? history : undefined,
        },
        {
          signal: controller.signal,
          onStep: (step) => {
            received.push(step);
            setLiveSteps([...received]);
          },
        },
      );

      // Cleared or left while the answer was on its way.
      if (requestRef.current !== controller) return;

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: resp.reply,
          steps: resp.steps,
          proposal: resp.proposal,
          limitReached: resp.limit_reached,
          baseYaml: latestYaml,
        },
      ]);
    } catch (err) {
      if (requestRef.current !== controller) return;
      const detail = timedOut
        ? "The assistant took too long to answer. Please try again."
        : err instanceof Error && err.message
          ? err.message
          : "An unexpected error occurred. Please try again.";
      toast.error(detail);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: detail,
          steps: received,
        },
      ]);
    } finally {
      clearTimeout(timer);
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
        setLiveSteps([]);
        inputRef.current?.focus();
      }
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  }

  function clearChat() {
    requestRef.current?.abort();
    requestRef.current = null;
    setLoading(false);
    setLiveSteps([]);
    setMessages([]);
    setUndo(null);
  }

  function setDecision(id: string, decision: ProposalDecision | undefined) {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, decision } : m)),
    );
  }

  function applyProposal(msg: Message) {
    if (!onApplyYaml || !msg.proposal) return;
    setUndo({
      messageId: msg.id,
      before: yamlRef.current,
      after: msg.proposal.yaml,
    });
    onApplyYaml(msg.proposal.yaml);
    setDecision(msg.id, "applied");
  }

  function undoApply() {
    if (!onApplyYaml || !undo) return;
    onApplyYaml(undo.before);
    setDecision(undo.messageId, undefined);
    setUndo(null);
  }

  return (
    <div className={cn("flex h-full flex-col", className)}>
      {/* Header */}
      <div className="flex items-center justify-between border-b px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
            <Sparkles className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-none">AI Assistant</h3>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Pipeline builder
            </p>
          </div>
        </div>
        {onClose && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </Button>
        )}
      </div>

      {/* Messages */}
      <ScrollArea
        ref={scrollRef}
        className="min-h-0 flex-1"
      >
        {messages.length === 0 ? (
          <div className="p-5 space-y-5">
            <div className="text-center py-8">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10">
                <Sparkles className="h-6 w-6 text-primary/70" />
              </div>
              <p className="text-sm font-medium">Pipeline AI Assistant</p>
              <p className="mx-auto mt-1.5 max-w-xs text-xs leading-relaxed text-muted-foreground">
                Describe what you want. I&apos;ll edit the pipeline YAML and
                show you the changes before anything reaches the editor.
              </p>
            </div>
            <div className="space-y-1.5">
              <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground px-1">
                Quick prompts
              </p>
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => sendMessage(prompt)}
                  className="w-full rounded-lg border px-3.5 py-2.5 text-left text-sm hover:bg-muted/50 transition-colors"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="p-5 space-y-5">
            {messages.map((msg) => (
              <div key={msg.id} className="flex gap-3">
                <div
                  className={cn(
                    "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
                    msg.role === "user"
                      ? "bg-primary/10 text-primary"
                      : "bg-muted text-muted-foreground",
                  )}
                >
                  {msg.role === "user" ? (
                    <User className="h-3.5 w-3.5" />
                  ) : (
                    <Bot className="h-3.5 w-3.5" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  {msg.steps && <StepSummary steps={msg.steps} />}
                  <ReplyText text={msg.content} />
                  {msg.proposal && (
                    <AiProposalCard
                      proposal={msg.proposal}
                      decision={msg.decision}
                      stale={
                        msg.baseYaml !== undefined &&
                        currentYaml !== msg.baseYaml
                      }
                      limitReached={msg.limitReached ?? false}
                      canUndo={
                        undo?.messageId === msg.id &&
                        currentYaml === undo.after
                      }
                      onApply={
                        onApplyYaml ? () => applyProposal(msg) : undefined
                      }
                      onDiscard={() => setDecision(msg.id, "discarded")}
                      onUndo={undoApply}
                    />
                  )}
                  {!msg.proposal && msg.limitReached && (
                    <p className="mt-1.5 text-xs text-warning">
                      The assistant stopped early.
                    </p>
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                  <Bot className="h-3.5 w-3.5" />
                </div>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    {liveSteps.length > 0 ? "Working..." : "Thinking..."}
                  </div>
                  <StepRows steps={liveSteps} />
                </div>
              </div>
            )}
          </div>
        )}
      </ScrollArea>

      {/* Input */}
      <div className="border-t p-4">
        <div className="relative">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe the pipeline you need..."
            rows={3}
            className="w-full resize-none rounded-lg border bg-transparent px-3.5 py-2.5 pr-12 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <button
            type="button"
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || loading}
            className="absolute bottom-2.5 right-2.5 rounded-lg p-2 text-primary hover:bg-primary/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
        <div className="mt-1.5 flex items-center justify-between">
          <p className="text-[10px] text-muted-foreground">
            Press Enter to send · Shift+Enter for new line
          </p>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={clearChat}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <Trash2 className="h-3 w-3" />
              Clear
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
```

What changed from the old panel: `YamlBlock` and "Apply to editor" on raw YAML are gone; requests go through `aiAssistantApi.stream`; steps are listed while the assistant works and summarised afterwards; replies render fenced code as code; each proposal renders an `AiProposalCard`; Apply records what to undo.

- [ ] **Step 3: Type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/pipeline/ai-proposal-card.tsx frontend/src/components/pipeline/ai-assistant-panel.tsx
git commit -m "feat(ai): review assistant changes as a diff

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: README

**Files:**
- Modify: `README.md` (one bullet in the feature list)

- [ ] **Step 1: Update the feature bullet**

Replace the bullet

```markdown
- **AI pipeline assistant** — chat-based interface that generates and refines YAML from natural-language prompts, with project-context awareness (available secrets / env vars) and one-click apply.
```

with:

```markdown
- **AI pipeline assistant** — chat-based interface that generates and refines YAML from natural-language prompts. It edits the pipeline with tools, checks the result with the pipeline validator, and shows the change as a diff you apply or discard. Aware of the project's secrets and env vars.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(ai): describe the assistant's diff review

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Final verification

- [ ] **Step 1: Backend suite**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `557 passed`.

- [ ] **Step 2: Frontend type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 3: No setting was added**

Run from the repository root:

```bash
git diff main --stat -- backend/app/config.py .env.example docker-compose.yml
```

Expected: no output.

- [ ] **Step 4: Manual checks (need the running app, a signed-in user and a configured AI provider)**

Report each as passed, failed or not run. Do not report them as passed without doing them.

1. Ask for a small change to an existing pipeline. Steps appear one at a time while the assistant works, then collapse to "N steps". A card shows only the changed lines with line numbers.
2. **Apply to editor** changes the editor; the card collapses to "Applied to editor" with **Undo**. Undo restores the editor and the card returns to pending. Ctrl+Z in the editor also reverts an apply.
3. **Discard** collapses the card and leaves the editor alone.
4. Edit the YAML by hand after a proposal arrives: the card says the editor changed and the button reads **Apply anyway**.
5. Ask a question ("what does this pipeline do?"): an answer, no card.
6. Ask for something invalid (for example a `kube_apply` step without `kubeconfig`, telling the assistant not to fix it): the card header shows the problem count and lists each problem with its line.
7. In an empty editor on the new-pipeline page, ask for a pipeline: the card is all additions.
8. On a pipeline page outside edit mode: the card has no Apply button and says to edit the pipeline.
9. Ask which agents exist, once as a user with `agents.read` and once without.
10. Configure a model without tool support (for example a small Ollama model): the request still ends with a card (`mode: "legacy"` in the response).
11. A diff longer than 400 lines is cut with a note; a long line scrolls sideways inside the card; the card does not widen the sheet at phone width.
12. Through the Next.js proxy (`NEXT_PUBLIC_API_URL` unset): steps still arrive one at a time, and a reply that takes longer than two minutes is not cut off.
