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
