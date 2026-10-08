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


def test_search_takes_the_pattern_as_plain_text_never_as_a_regular_expression():
    assert doc().search(r"^\s+- run: make \w+$") == ("No matches.", 0)
    assert doc().search("make .*") == ("No matches.", 0)
    text, count = WorkingDocument("a: (x+)+$\nb: 1\n").search("(x+)+$")
    assert count == 1 and "> 1 | a: (x+)+$" in text


def test_search_reports_no_matches_and_rejects_bad_input():
    assert doc().search("nothing-here") == ("No matches.", 0)
    with pytest.raises(DocumentError):
        doc().search("")


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
