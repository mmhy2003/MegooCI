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
    c.begin_turn()
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


async def test_search_has_no_regular_expression_mode():
    """A regular expression can take hours on one line and cannot be interrupted."""
    search = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "search")
    assert list(search["function"]["parameters"]["properties"]) == ["pattern"]
    out = await call(ctx(), "search", pattern="^name", regex=True)
    assert out.ok and out.text == "No matches."


async def test_a_line_numbered_edit_after_one_that_moved_the_lines_is_refused():
    """Models send several edits in one answer, numbered against the YAML as
    it was before the first of them."""
    c = ctx()
    c.begin_turn()
    first = await call(c, "insert_lines", after=1, text="env:\n  A: b\n")
    after_first = c.document.text
    second = await call(c, "replace_lines", start=5, end=5, text="      - run: other")
    third = await call(c, "insert_lines", after=5, text="      - run: more")

    assert first.ok
    assert second.ok is False and third.ok is False
    assert "line numbers" in second.text and "read" in second.text.lower()
    assert c.document.text == after_first, "the refused edits changed nothing"

    c.begin_turn()  # the model has seen the results and answers again
    assert (await call(c, "replace_lines", start=7, end=7, text="      - run: other")).ok
    assert c.document.text.splitlines()[6] == "      - run: other"


async def test_edits_that_keep_the_line_count_do_not_block_later_ones():
    c = ctx()
    c.begin_turn()
    assert (await call(c, "replace_lines", start=5, end=5, text="      - run: a")).ok
    assert (await call(c, "replace_text", old="name: demo", new="name: renamed")).ok
    assert (await call(c, "replace_lines", start=3, end=3, text="  - name: compile")).ok


async def test_text_edits_and_reads_still_work_after_the_lines_moved():
    c = ctx()
    c.begin_turn()
    await call(c, "replace_lines", start=5, end=5, text="      - run: a\n      - run: b")
    assert (await call(c, "replace_text", old="run: b", new="run: c")).ok
    assert (await call(c, "read_lines")).ok
    assert (await call(c, "validate")).ok
