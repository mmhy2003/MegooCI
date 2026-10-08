"""The AI assistant prompt teaches the notifications block the validator accepts."""

import os
import re
import sys
from unittest.mock import MagicMock

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is an optional dep not installed in the test venv; stub it before
# importing the module under test so the module-level `import litellm` succeeds.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.services.pipeline_compiler import (
    NOTIFICATION_ENTRY_FIELDS,
    validate_pipeline,
)


def _prompt() -> str:
    from app.api.v1.ai_assistant import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _section(heading: str) -> str:
    prompt = _prompt()
    start = prompt.index(heading)
    end = prompt.index("\n## ", start + 1)
    return prompt[start:end]


def _yaml_examples(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)


def test_prompt_has_a_notifications_section_after_runs_on():
    prompt = _prompt()
    assert (
        prompt.index("## Targeting agents (runs_on)")
        < prompt.index("## Notifications")
        < prompt.index("## Pipeline Structure")
    )


def test_notifications_example_is_a_valid_pipeline_with_both_entry_forms():
    examples = _yaml_examples(_section("## Notifications"))
    assert len(examples) == 1
    assert validate_pipeline(examples[0]) == []

    from app.services.build_notifications import failure_notifications

    entries = failure_notifications(examples[0])
    assert [e.channel for e in entries] == ["deploy-alerts", "ops-email"]
    assert entries[1].recipient and entries[1].subject and entries[1].message


def test_notifications_section_names_every_entry_field_and_placeholder():
    section = _section("## Notifications")
    for field in NOTIFICATION_ENTRY_FIELDS:
        assert f"`{field}`" in section, field
    for placeholder in ("${{ build.failed_stage }}", "${{ build.failed_step }}", "${{ build.url }}"):
        assert placeholder in section, placeholder


def test_pipeline_structure_example_shows_the_block_and_still_validates():
    examples = _yaml_examples(_section("## Pipeline Structure"))
    assert len(examples) == 1
    assert "notifications:" in examples[0]
    assert validate_pipeline(examples[0]) == []


def test_notify_step_section_says_it_cannot_report_a_failure():
    prompt = _prompt()
    start = prompt.index("### notify")
    section = prompt[start:prompt.index("\n### ", start + 1)]
    assert "never reports a failure" in section
    assert "`notifications` block" in section


def test_notify_example_uses_a_placeholder_that_exists():
    prompt = _prompt()
    assert "${{ build.commit_sha }}" not in prompt
    assert "Commit: ${{ build.commit }}" in prompt


def test_rules_tell_the_assistant_to_use_the_block_for_failures():
    prompt = _prompt()
    rules = prompt[prompt.index("## Rules"):]
    assert "12. When the user wants to be told about failed builds" in rules
    assert "`notifications` block with `on_failure`" in rules
