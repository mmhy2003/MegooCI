"""What the AI assistant is told about notification events must be true: its
examples are run through the real validator."""
import os
import re
import sys
from unittest.mock import MagicMock

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; stub it so the module imports.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1.ai_assistant import REFERENCE_TOPICS, SYSTEM_PROMPT, TOOL_SYSTEM_PROMPT
from app.services.build_notifications import notifications_by_event
from app.services.pipeline_compiler import NOTIFICATION_EVENTS, validate_pipeline

def _section(heading: str) -> str:
    start = SYSTEM_PROMPT.index(heading)
    end = SYSTEM_PROMPT.find("\n## ", start + 1)
    return SYSTEM_PROMPT[start:end if end != -1 else None]


def _step_section(name: str) -> str:
    start = SYSTEM_PROMPT.index(f"### {name}")
    return SYSTEM_PROMPT[start:SYSTEM_PROMPT.index("\n### ", start + 1)]


def _yaml_examples(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)


# ── the assistant's prompt ──────────────────────────────────────────────

def test_the_section_names_every_event():
    section = _section("## Notifications")
    for event in NOTIFICATION_EVENTS:
        assert f"`{event}`" in section, event
    assert "is the only event" not in SYSTEM_PROMPT


def test_the_example_is_valid_and_shows_three_events():
    examples = _yaml_examples(_section("## Notifications"))
    assert len(examples) == 1
    assert validate_pipeline(examples[0]) == []

    by_event = notifications_by_event(examples[0])
    assert list(by_event) == ["on_start", "on_failure", "on_complete"]
    assert by_event["on_start"][0].channel == by_event["on_complete"][0].channel == "team-chat"


def test_the_section_explains_the_one_message_per_channel_rule():
    section = " ".join(_section("## Notifications").split())
    assert "gets one message, from the most specific event" in section
    assert "`on_fixed` before `on_success` before `on_complete`" in section
    assert "`on_failure` or `on_cancelled` before `on_complete`" in section


def test_the_section_defines_fixed_and_what_a_build_that_never_started_sends():
    section = " ".join(_section("## Notifications").split())
    assert ("the last build of the same pipeline and branch that succeeded or failed "
            "(cancelled builds are skipped) had failed") in section
    assert "previous finished build" not in section
    assert "stays pending and sends nothing" in section
    assert "cancelled before it started sends no `on_cancelled` and no `on_complete`" in section


def test_the_section_names_the_placeholders_for_every_event():
    section = _section("## Notifications")
    for placeholder in ("${{ build.status }}", "${{ build.url }}", "${{ build.failed_stage }}",
                        "${{ build.failed_step }}", "${{ build.waiting_stage }}",
                        "${{ build.waiting_step }}"):
        assert placeholder in section, placeholder
    for status in ("`running`", "`success`", "`failed`", "`cancelled`"):
        assert status in section, status


def test_the_structure_example_still_validates_and_hints_at_the_events():
    examples = _yaml_examples(_section("## Pipeline Structure"))
    assert len(examples) == 1
    assert validate_pipeline(examples[0]) == []
    assert "on_start, on_complete" in examples[0]


def test_wait_input_points_to_on_waiting_and_notify_to_the_block():
    assert "`on_waiting`" in _step_section("wait_input")
    notify = " ".join(_step_section("notify").split())
    assert "never reports a failure" in notify
    assert "a build starting or ending" in notify


def test_the_rules_send_the_assistant_to_the_block_for_every_event():
    rules = " ".join(SYSTEM_PROMPT[SYSTEM_PROMPT.index("## Rules"):].split())
    for event in ("on_start", "on_complete", "on_failure", "on_cancelled", "on_fixed", "on_waiting"):
        assert f"`{event}`" in rules, event
    assert "does not run after a failure or a cancellation" in rules


def test_tool_mode_knows_the_events_through_its_rule_and_the_reference_topics():
    rules = " ".join(TOOL_SYSTEM_PROMPT.split())
    assert "starting, finishing, failing, being cancelled, recovering or waiting for approval" in rules
    assert "Read the `notifications` topic" in rules
    for event in NOTIFICATION_EVENTS:
        assert f"`{event}`" in REFERENCE_TOPICS["notifications"], event
    assert "`on_waiting`" in REFERENCE_TOPICS["wait_input"]
    assert "on_start, on_complete" in REFERENCE_TOPICS["structure"]
