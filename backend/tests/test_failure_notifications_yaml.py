"""Validation of the top-level `notifications` block in pipeline YAML."""

import pytest

from app.services.pipeline_compiler import (
    compile_to_build_graph,
    normalize_runs_on,
    parse_yaml_pipeline,
    validate_pipeline,
    validate_pipeline_definition,
)

STAGES = (
    "stages:\n"
    "  - name: deploy\n"
    "    steps:\n"
    "      - run: ./deploy.sh\n"
)


def _pipeline(notifications_block: str) -> str:
    return "name: demo\n" + notifications_block + STAGES


SHORT = _pipeline(
    "notifications:\n"
    "  on_failure:\n"
    "    - deploy-alerts\n"
)

FULL = _pipeline(
    "notifications:\n"
    "  on_failure:\n"
    "    - deploy-alerts\n"
    "    - channel: ops-email\n"
    "      recipient: oncall@example.com\n"
    "      subject: \"Staging deploy failed\"\n"
    "      message: |\n"
    "        Build #${{ build.number }} of ${{ pipeline.name }} failed\n"
    "        at ${{ build.failed_stage }} / ${{ build.failed_step }}\n"
    "        ${{ build.url }}\n"
)


def test_short_form_passes():
    assert validate_pipeline(SHORT) == []


def test_full_form_and_both_together_pass():
    assert validate_pipeline(FULL) == []


def test_same_channel_may_appear_twice():
    block = (
        "notifications:\n"
        "  on_failure:\n"
        "    - channel: ops-email\n"
        "      recipient: a@example.com\n"
        "    - channel: ops-email\n"
        "      recipient: b@example.com\n"
    )
    assert validate_pipeline(_pipeline(block)) == []


def test_pipeline_without_the_block_is_unaffected():
    assert validate_pipeline(_pipeline("")) == []


def test_block_does_not_change_what_the_pipeline_compiles_to():
    with_block = compile_to_build_graph(parse_yaml_pipeline(FULL))
    without = compile_to_build_graph(parse_yaml_pipeline(_pipeline("")))
    assert with_block == without
    assert normalize_runs_on(parse_yaml_pipeline(FULL).get("runs_on")) is None


@pytest.mark.parametrize(
    "block, expected",
    [
        ("notifications: deploy-alerts\n", "'notifications' must be a mapping"),
        ("notifications: [deploy-alerts]\n", "'notifications' must be a mapping"),
        ("notifications: {}\n", "'notifications' requires at least one event (on_start, "),
        (
            "notifications:\n  on_fail:\n    - deploy-alerts\n",
            "'notifications' has unknown key(s): on_fail (allowed: on_start, on_waiting, "
            "on_success, on_fixed, on_failure, on_cancelled, on_complete)",
        ),
        (
            "notifications:\n  on_failure: deploy-alerts\n",
            "'notifications.on_failure' must be a non-empty list",
        ),
        (
            "notifications:\n  on_failure: []\n",
            "'notifications.on_failure' must be a non-empty list",
        ),
        (
            "notifications:\n  on_failure:\n",
            "'notifications.on_failure' must be a non-empty list",
        ),
        (
            "notifications:\n  on_failure:\n    - 42\n",
            "notifications.on_failure[0]: must be a channel name or a mapping",
        ),
        (
            "notifications:\n  on_failure:\n    - \"  \"\n",
            "notifications.on_failure[0]: channel name must not be empty",
        ),
        (
            "notifications:\n  on_failure:\n    - recipient: a@example.com\n",
            "notifications.on_failure[0]: requires 'channel'",
        ),
        (
            "notifications:\n  on_failure:\n    - channel: \"\"\n",
            "notifications.on_failure[0]: requires 'channel'",
        ),
        (
            "notifications:\n  on_failure:\n    - chanel: deploy-alerts\n",
            "notifications.on_failure[0]: unknown field(s): chanel "
            "(allowed: channel, message, subject, recipient)",
        ),
        (
            "notifications:\n  on_failure:\n    - channel: a\n      message: \"\"\n",
            "notifications.on_failure[0]: 'message' must be a non-empty string",
        ),
        (
            "notifications:\n  on_failure:\n    - channel: a\n      subject: 5\n",
            "notifications.on_failure[0]: 'subject' must be a non-empty string",
        ),
        (
            "notifications:\n  on_failure:\n    - channel: a\n      recipient: [x]\n",
            "notifications.on_failure[0]: 'recipient' must be a non-empty string",
        ),
    ],
)
def test_block_rules(block, expected):
    errors = validate_pipeline(_pipeline(block))
    assert any(expected in e for e in errors), errors


def test_second_entry_is_named_by_its_index():
    block = (
        "notifications:\n"
        "  on_failure:\n"
        "    - deploy-alerts\n"
        "    - recipient: a@example.com\n"
    )
    errors = validate_pipeline(_pipeline(block))
    assert any("notifications.on_failure[1]: requires 'channel'" in e for e in errors), errors


def test_entry_error_carries_the_entry_line():
    block = (
        "notifications:\n"        # line 2
        "  on_failure:\n"         # line 3
        "    - deploy-alerts\n"   # line 4
        "    - chanel: ops\n"     # line 5
    )
    errors = validate_pipeline_definition(_pipeline(block))
    match = [e for e in errors if "unknown field(s): chanel" in e.message]
    assert match, errors
    assert match[0].line == 5


def test_block_error_carries_the_block_line():
    block = (
        "notifications:\n"        # line 2
        "  on_fail:\n"            # line 3
        "    - deploy-alerts\n"
    )
    errors = validate_pipeline_definition(_pipeline(block))
    match = [e for e in errors if "unknown key(s): on_fail" in e.message]
    assert match, errors
    assert match[0].line == 3


def test_notification_errors_are_reported_together_with_stage_errors():
    yaml_doc = (
        "name: demo\n"
        "notifications:\n"
        "  on_fail: []\n"
        "stages:\n"
        "  - name: deploy\n"
        "    steps:\n"
        "      - kube_apply:\n"
        "          manifests: [k8s/]\n"
    )
    errors = validate_pipeline(yaml_doc)
    assert any("unknown key(s): on_fail" in e for e in errors), errors
    assert any("requires 'kubeconfig'" in e for e in errors), errors
