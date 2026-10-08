"""Validation of every event in the top-level `notifications` block."""

import pytest

from app.services.pipeline_compiler import (
    NOTIFICATION_EVENTS,
    compile_to_build_graph,
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
NEW_EVENTS = ("on_start", "on_waiting", "on_success", "on_fixed", "on_cancelled", "on_complete")


def _pipeline(notifications_block: str) -> str:
    return "name: demo\n" + notifications_block + STAGES


def test_the_seven_events_in_the_order_they_are_listed_to_authors():
    assert NOTIFICATION_EVENTS == (
        "on_start", "on_waiting", "on_success", "on_fixed", "on_failure", "on_cancelled",
        "on_complete",
    )


@pytest.mark.parametrize("event", NEW_EVENTS)
def test_each_new_event_accepts_both_entry_forms(event):
    block = (
        "notifications:\n"
        f"  {event}:\n"
        "    - team-chat\n"
        "    - channel: ops-email\n"
        "      recipient: oncall@example.com\n"
        "      subject: \"Deploy\"\n"
        "      message: \"Build #${{ build.number }}: ${{ build.status }}\"\n"
    )
    assert validate_pipeline(_pipeline(block)) == []


def test_all_events_together_and_one_channel_under_several():
    block = "notifications:\n" + "".join(
        f"  {event}:\n    - team-chat\n" for event in NOTIFICATION_EVENTS
    )
    assert validate_pipeline(_pipeline(block)) == []


def test_a_block_without_on_failure_is_valid():
    assert validate_pipeline(_pipeline("notifications:\n  on_start:\n    - team-chat\n")) == []


def test_an_empty_block_names_the_events_it_could_have():
    errors = validate_pipeline(_pipeline("notifications: {}\n"))
    assert errors == [
        "'notifications' requires at least one event (on_start, on_waiting, on_success, "
        "on_fixed, on_failure, on_cancelled, on_complete)"
    ]


def test_the_old_name_on_finish_is_an_unknown_key():
    errors = validate_pipeline(_pipeline("notifications:\n  on_finish:\n    - team-chat\n"))
    assert len(errors) == 1
    assert errors[0].startswith("'notifications' has unknown key(s): on_finish (allowed: on_start, ")


@pytest.mark.parametrize("event", NEW_EVENTS)
@pytest.mark.parametrize("value", ["team-chat", "[]", "{channel: team-chat}"])
def test_each_event_must_be_a_non_empty_list(event, value):
    errors = validate_pipeline(_pipeline(f"notifications:\n  {event}: {value}\n"))
    assert errors == [
        f"'notifications.{event}' must be a non-empty list of channel names or mappings"
    ]


@pytest.mark.parametrize(
    "entry, expected",
    [
        ("    - \"\"\n", "channel name must not be empty"),
        ("    - 42\n", "must be a channel name or a mapping"),
        ("    - recipient: a@example.com\n", "requires 'channel'"),
        ("    - channel: team-chat\n      chanel: x\n", "unknown field(s): chanel (allowed: channel, message, subject, recipient)"),
        ("    - channel: team-chat\n      message: \"\"\n", "'message' must be a non-empty string"),
        ("    - channel: team-chat\n      subject: 5\n", "'subject' must be a non-empty string"),
        ("    - channel: team-chat\n      recipient: []\n", "'recipient' must be a non-empty string"),
    ],
)
def test_entry_rules_apply_to_a_new_event(entry, expected):
    errors = validate_pipeline(_pipeline("notifications:\n  on_complete:\n    - ok-channel\n" + entry))
    assert errors == [f"notifications.on_complete[1]: {expected}"]


def test_mistakes_in_two_events_are_both_reported_with_their_lines():
    block = (
        "notifications:\n"            # line 2
        "  on_start:\n"               # line 3
        "    - channel: team-chat\n"  # line 4
        "      mesage: hi\n"
        "  on_failure:\n"             # line 6
        "    - team-chat\n"
        "  on_complete:\n"            # line 8
        "    - team-chat\n"
        "    - subject: Done\n"       # line 10
    )
    errors = validate_pipeline_definition(_pipeline(block))
    assert [(e.message, e.line) for e in errors] == [
        ("notifications.on_start[0]: unknown field(s): mesage "
         "(allowed: channel, message, subject, recipient)", 4),
        ("notifications.on_complete[1]: requires 'channel'", 10),
    ]


def test_a_bad_event_does_not_hide_a_mistake_in_a_later_one():
    block = (
        "notifications:\n"
        "  on_start: nope\n"
        "  on_complete:\n"
        "    - recipient: a@example.com\n"
    )
    errors = validate_pipeline(_pipeline(block))
    assert errors == [
        "'notifications.on_start' must be a non-empty list of channel names or mappings",
        "notifications.on_complete[0]: requires 'channel'",
    ]


def test_an_unknown_key_beside_a_valid_event_is_one_error():
    block = "notifications:\n  on_start:\n    - team-chat\n  on_done:\n    - team-chat\n"
    errors = validate_pipeline(_pipeline(block))
    assert len(errors) == 1 and "unknown key(s): on_done" in errors[0]


def test_the_block_never_changes_what_the_pipeline_compiles_to():
    block = "notifications:\n" + "".join(
        f"  {event}:\n    - team-chat\n" for event in NOTIFICATION_EVENTS
    )
    assert compile_to_build_graph(parse_yaml_pipeline(_pipeline(block))) == compile_to_build_graph(
        parse_yaml_pipeline(_pipeline(""))
    )
