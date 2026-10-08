"""What the pipeline docs panel says about notification events must be true.

The frontend has no test harness, so the panel's text and YAML example are
read out of its source file and the example is run through the real validator.
"""
import pathlib
import re

import pytest

from app.services.build_notifications import notifications_by_event
from app.services.pipeline_compiler import NOTIFICATION_EVENTS, validate_pipeline

DOCS_PANEL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "components" / "pipeline" / "docs-panel.tsx"
)


def _panel_section(section_id: str) -> dict[str, str]:
    """The title, description and YAML example of one docs-panel section."""
    if not DOCS_PANEL.exists():
        pytest.skip("the frontend sources are not part of this checkout")
    source = DOCS_PANEL.read_text(encoding="utf-8")
    match = re.search(
        r'id: "' + re.escape(section_id) + r'",\s*title: "(?P<title>[^"]*)",.*?'
        r'description:\s*"(?P<description>(?:[^"\\]|\\.)*)",\s*yaml: `(?P<yaml>.*?)`,',
        source, flags=re.DOTALL,
    )
    assert match, f"no docs-panel section with id {section_id!r}"
    fields = match.groupdict()
    fields["yaml"] = fields["yaml"].replace("\\${{", "${{")
    return fields


def test_docs_panel_section_lists_every_event_and_the_rules():
    section = _panel_section("notifications")
    assert section["title"] == "Build Notifications"
    for event in NOTIFICATION_EVENTS:
        assert event in section["description"], event
    assert "one message" in section["description"]
    assert "never started sends nothing" in section["description"]
    assert "Successful and cancelled builds send nothing" not in section["description"]


def test_docs_panel_example_is_valid_and_shows_the_overlap():
    section = _panel_section("notifications")
    assert validate_pipeline(section["yaml"]) == []

    by_event = notifications_by_event(section["yaml"])
    assert list(by_event) == ["on_start", "on_failure", "on_complete"]
    shared = {entry.channel for entry in by_event["on_failure"]} & {
        entry.channel for entry in by_event["on_complete"]}
    assert shared, "the example shows one channel under on_failure and on_complete"
    assert any(entry.message for entry in by_event["on_failure"])


def test_docs_panel_approval_and_notify_sections_point_to_the_block():
    assert "on_waiting" in _panel_section("wait_input")["description"]
    notify = _panel_section("notify")["description"]
    assert "Build Notifications" in notify and "Failure Notifications" not in notify
