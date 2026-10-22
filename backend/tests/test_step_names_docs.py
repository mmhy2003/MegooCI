"""What the assistant prompt and the docs panel say about step names must be
true: their examples go through the real validator and compiler."""
import os
import pathlib
import re
import sys
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; stub it so the module imports.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

import yaml

from app.api.v1.ai_assistant import REFERENCE_TOPICS, SYSTEM_PROMPT, TOOL_SYSTEM_PROMPT
from app.services.pipeline_compiler import (
    compile_to_build_graph,
    parse_yaml_pipeline,
    validate_pipeline,
)

DOCS_PANEL = (
    pathlib.Path(__file__).resolve().parents[2]
    / "frontend" / "src" / "components" / "pipeline" / "docs-panel.tsx"
)


def _flat(text: str) -> str:
    return " ".join(text.split())


def _yaml_examples(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)


def _steps(pipeline_yaml: str) -> list:
    data = yaml.safe_load(pipeline_yaml)
    return [step for stage in data["stages"] for step in stage["steps"]]


def _panel_section(section_id: str) -> dict[str, str]:
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


# ── the assistant's prompt ──────────────────────────────────────────────

def test_the_structure_section_explains_names_and_the_automatic_label():
    structure = _flat(REFERENCE_TOPICS["structure"])
    assert "`name`" in structure
    assert "255 characters" in structure
    assert "label" in structure and "step-0" not in structure


def test_the_structure_example_names_every_step():
    example = _yaml_examples(REFERENCE_TOPICS["structure"])[0]
    assert validate_pipeline(example) == []
    assert all(isinstance(step, dict) and step.get("name") for step in _steps(example))


def test_both_prompts_tell_the_assistant_to_name_every_step():
    assert "name every step" in _flat(SYSTEM_PROMPT[SYSTEM_PROMPT.index("## Rules"):])
    assert "name every step" in _flat(TOOL_SYSTEM_PROMPT)
    assert "## Pipeline Structure" in TOOL_SYSTEM_PROMPT


# ── the docs panel ──────────────────────────────────────────────────────

def test_the_docs_panel_has_a_section_on_naming_steps():
    section = _panel_section("step_names")
    assert section["title"] == "Naming Steps"
    description = _flat(section["description"])
    assert "255 characters" in description
    assert "label" in description

    assert validate_pipeline(section["yaml"]) == []
    stages = compile_to_build_graph(parse_yaml_pipeline(section["yaml"]))
    names = [step["name"] for stage in stages for step in stage["steps"]]
    assert any(not name.startswith("step-") for name in names)


def test_the_docs_panel_structure_example_names_every_step():
    example = _panel_section("structure")["yaml"]
    assert validate_pipeline(example) == []
    assert all(isinstance(step, dict) and step.get("name") for step in _steps(example))
