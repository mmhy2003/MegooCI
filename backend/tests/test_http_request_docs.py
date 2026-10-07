"""The AI assistant prompt teaches http_request syntax that the validator accepts."""

import os
import re
import sys
import textwrap
from unittest.mock import MagicMock

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is an optional dep not installed in the test venv; stub it before
# importing the module under test so the module-level `import litellm` succeeds.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.services.pipeline_compiler import HTTP_REQUEST_FIELDS, validate_pipeline


def _prompt() -> str:
    from app.api.v1.ai_assistant import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def _http_request_section() -> str:
    prompt = _prompt()
    start = prompt.index("### http_request")
    end = prompt.index("\n### ", start + 1)
    return prompt[start:end]


def _yaml_examples(text: str) -> list[str]:
    return re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)


def _as_pipeline(steps_yaml: str) -> str:
    return (
        "name: example\n"
        "stages:\n"
        "  - name: example\n"
        "    steps:\n" + textwrap.indent(steps_yaml, "      ")
    )


def test_prompt_has_an_http_request_section_with_both_body_forms():
    examples = _yaml_examples(_http_request_section())
    assert len(examples) >= 2
    assert any("json:" in e for e in examples)
    assert any("body:" in e for e in examples)


def test_every_http_request_example_in_the_prompt_is_valid():
    for example in _yaml_examples(_http_request_section()):
        assert validate_pipeline(_as_pipeline(example)) == [], example


def test_prompt_names_every_field():
    section = _http_request_section()
    for field in HTTP_REQUEST_FIELDS:
        assert f"`{field}`" in section, field


def test_section_sits_between_kube_apply_and_wait_webhook():
    prompt = _prompt()
    assert (
        prompt.index("### kube_apply")
        < prompt.index("### http_request")
        < prompt.index("### wait_webhook")
    )


def test_secret_rule_covers_http_request_urls():
    """Rule 7 used to forbid webhook URLs in YAML outright; http_request needs
    them, taken from a secret."""
    prompt = _prompt()
    rules = prompt[prompt.index("## Rules"):]
    assert "http_request" in rules
    assert "never hardcode webhook URLs" in rules


def test_placeholders_section_lists_build_context():
    prompt = _prompt()
    section = prompt[prompt.index("## Placeholders"):prompt.index("## Rules")]
    for placeholder in ("${{ build.number }}", "${{ build.branch }}", "${{ pipeline.name }}"):
        assert placeholder in section, placeholder
