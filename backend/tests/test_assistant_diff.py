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
