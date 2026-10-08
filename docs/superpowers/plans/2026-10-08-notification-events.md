# More Build Notification Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add six events to a pipeline's `notifications` block — `on_start`, `on_waiting`, `on_success`, `on_fixed`, `on_cancelled`, `on_complete` — beside the existing `on_failure`, and teach the pipeline docs panel, the README and the AI assistant about them.

**Architecture:** The validator accepts seven events with today's entry rules. The notification module, which today reads and sends `on_failure` for a failed build, is generalised: it reads every event, decides which entries a moment of a build calls for (most specific event wins per destination), words a default message per event, and sends. The build executor snapshots the build at three moments — start, reaching a `wait_input` step, end — and hands each snapshot to that module: in the background for the first two, after the agent is released for the last. Nothing runs on the agent and the database schema does not change.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2 async, PyYAML, pytest with `asyncio_mode=auto` and in-memory SQLite (`tests/_rbac.py`, `tests/_failure_notifications.py`); Next.js + TypeScript for the docs panel.

**Spec:** `docs/superpowers/specs/2026-10-08-notification-events-design.md`

## Global Constraints

- **Branch:** all work happens on `feat/notification-events`. Never commit to `main`.
- **Commits:** conventional style (`refactor(builds): …`, `feat(pipeline): …`, `feat(builds): …`, `feat(ai): …`, `docs(pipeline): …`). End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **The seven events, in this order everywhere they are listed:** `on_start`, `on_waiting`, `on_success`, `on_fixed`, `on_failure`, `on_cancelled`, `on_complete`. The name is `on_complete`, not `on_finish`.
- **Entry rules are today's `on_failure` rules, for every event:** a non-empty channel name, or a mapping with only `channel` (required), `message`, `subject`, `recipient`, each a non-empty string. Each event is a non-empty list. The block needs at least one event.
- **`on_failure` behaves exactly as before.** The existing failure-notification tests keep passing; the only edits to them are the ones this plan prints.
- **One message per destination when a build ends.** A destination is a channel name plus the entry's `recipient`. Order: `on_fixed`, `on_success`, `on_complete` for a success (`on_fixed` only when it applies); `on_failure`, `on_complete`; `on_cancelled`, `on_complete`. Entries within one event are never dropped.
- **Fixed** means: the most recent earlier build of the same pipeline and the same branch that ended as `success` or `failed` ended as `failed`. Builds with no branch (NULL or empty) are one branch. The lookup is made only when the pipeline lists `on_fixed`.
- **A build that never ran sends nothing.** End events are sent by the executor only.
- **`on_waiting` fires for `wait_input` steps only,** never for `wait_webhook`.
- **Server-side only.** No change to the agent, the database schema, or the compiled build graph.
- **Best-effort.** Nothing in this feature may change a build's status, delay a step, or raise into the executor. `on_start` and `on_waiting` are sent in the background; end events after the agent is released and the next build dispatched, and after this build's background sends.
- **Never put a provider's error text in the build log.**
- **The block is read from the pipeline's YAML as stored when the event happens.**
- **New placeholders, only two:** `${{ build.waiting_stage }}`, `${{ build.waiting_step }}`. `${{ build.status }}` is `running` for `on_start` and `on_waiting`, otherwise the outcome.
- **Backend tests:** run from `backend/` with `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`. The suite has 578 tests before this plan and 724 after it.
- **Frontend gate:** `npx tsc --noEmit` from `frontend/`. There is no frontend unit-test harness, and `npm run lint` is broken; do not use it.
- **Line endings:** existing files use CRLF in the working tree. Keep them: use the Edit tool, not a script that rewrites a file with LF.
- **Edits are exact.** Where a step says "Replace … with …", the first block occurs exactly once in the file; replace that text and nothing else.
- **Paths in commands:** `git` commands use paths relative to the repository root. Commands are written for Git Bash.

## Review Focus

Inputs the spec implies but does not spell out. Each has a test in the task that owns the code.

1. **The natural way to write the block: a channel under `on_complete` and again under `on_failure` with its own message.** Expected: a failed build sends that channel one message, the custom one. Pinned in Task 3 (`test_a_failed_build_sends_one_message_to_a_channel_under_two_events`).
2. **A slow or stalled channel on `on_start` or `on_waiting`.** Expected: the first step, and the approval step, begin without waiting for it. Pinned in Task 4 (`test_the_first_step_does_not_wait_for_the_start_message`, `test_the_approval_step_does_not_wait_for_the_message`).
3. **A build that fails within moments of starting.** Expected: "started" still arrives before "failed". Pinned in Task 4 (`test_the_end_message_waits_for_a_start_message_still_being_sent`).
4. **A build cancelled before it ran, or replaced by a newer trigger.** Expected: no message at all; otherwise every superseded trigger would announce a cancellation. Pinned in Task 4 (`test_a_build_that_is_not_pending_is_not_run_and_sends_nothing`).
5. **A red feature branch followed by a normal green `main` build; builds with no branch.** Expected: not announced as fixed; branchless builds compare with each other. Pinned in Task 3 (`test_a_failure_on_another_branch_does_not_count`, `test_builds_without_a_branch_are_one_branch`).
6. **The same email channel with two recipients under two events.** Expected: both are sent; they are different destinations. Pinned in Task 3 (`test_the_same_channel_with_another_recipient_is_another_destination`).
7. **Someone edits the pipeline's YAML while a build of it runs.** Expected: each event uses the block as stored at that moment. Pinned in Task 4 (`test_the_block_is_read_when_the_event_happens_not_when_the_build_was_triggered`).
8. **The notification code itself throws, or the block cannot be read.** Expected: the build runs and ends as it would have. Pinned in Task 4 (`test_a_send_that_raises_never_changes_the_build`, `test_a_pipeline_whose_notifications_cannot_be_read_still_builds`).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/services/pipeline_compiler.py` | Modify | The event list; `_notification_errors` for every event. |
| `backend/app/services/build_notifications.py` | Rewrite | Read the block, pick the entries for a moment, word the message, send. |
| `backend/app/services/build_executor.py` | Modify | Snapshot at start, at an approval step and at the end; background sends; ordering. |
| `backend/app/api/v1/ai_assistant.py` | Modify | The prompt's notifications section, structure example, `wait_input` and `notify` notes, rule 12. |
| `backend/app/services/assistant/reference.py` | Modify | Tool-mode rule 10. |
| `frontend/src/components/pipeline/docs-panel.tsx` | Modify | "Build Notifications" section; `wait_input` and `notify` descriptions. |
| `README.md` | Modify | Example and event table. |
| `backend/tests/test_notification_events_yaml.py` | Create | Validator tests for the new events. |
| `backend/tests/test_notification_events.py` | Create | Reading, selection, wording, "fixed", sending. |
| `backend/tests/test_notification_events_executor.py` | Create | The executor's three moments. |
| `backend/tests/test_notification_events_docs.py` | Create | The assistant's prompt. |
| `backend/tests/test_notification_events_docs_panel.py` | Create | The docs panel's text and example. |
| `backend/tests/test_failure_notifications_*.py`, `backend/tests/test_build_notifications.py` | Modify | Only the call sites and expectations this plan prints. |

---

### Task 1: Names that fit every event

A rename with no change in behaviour, so that the later tasks do not describe a build's start with a class called `FailedBuild`.

**Files:**
- Modify: `backend/app/services/build_notifications.py`, `backend/app/services/build_executor.py`
- Modify: `backend/tests/test_build_notifications.py`, `backend/tests/test_failure_notifications_executor.py`

**Interfaces:**
- Produces (renamed, same behaviour):

| Old name | New name |
|---|---|
| `FailureNotification` | `Notification` |
| `FailedBuild` | `BuildSnapshot` |
| `send_failure_notifications` | `send_build_notifications` |
| `_capture_failure` (executor) | `_capture_snapshot` |
| `_notify_build_failure` (executor) | `_send_notifications` |

- Unchanged in this task: `failure_notifications`, `default_subject`, `default_message`, `failed_step_of`, `SEND_TIMEOUT_SECONDS`.

- [ ] **Step 1: Rename, whole words only, in the four files**

Save this as a temporary file outside the repository, run it from `backend/` with `./.venv/Scripts/python.exe <path to the file>`, then delete it. It keeps each file's line endings.

```python
"""Task 1: give the notification code names that fit every event.

Run from backend/. Renames whole words only and keeps each file's line endings.
"""
import pathlib
import re

RENAMES = {
    "FailureNotification": "Notification",
    "FailedBuild": "BuildSnapshot",
    "send_failure_notifications": "send_build_notifications",
    "_capture_failure": "_capture_snapshot",
    "_notify_build_failure": "_send_notifications",
}
FILES = [
    "app/services/build_notifications.py",
    "app/services/build_executor.py",
    "tests/test_build_notifications.py",
    "tests/test_failure_notifications_executor.py",
]

for name in FILES:
    path = pathlib.Path(name)
    text = path.read_bytes().decode("utf-8")
    count = 0
    for old, new in RENAMES.items():
        text, n = re.subn(rf"(?<![A-Za-z0-9_]){re.escape(old)}(?![A-Za-z0-9_])", new, text)
        count += n
    path.write_bytes(text.encode("utf-8"))
    print(f"{name}: {count} replacements")
```

Expected output: `9`, `12`, `14` and `12` replacements for the four files, in that order.

- [ ] **Step 2: Check that no old name is left**

Run from the repository root:

```bash
grep -rnE "FailureNotification|FailedBuild|send_failure_notifications|_capture_failure|_notify_build_failure" backend/app backend/tests
```

Expected: no output.

- [ ] **Step 3: Run the whole backend suite**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `578 passed`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/build_notifications.py backend/app/services/build_executor.py backend/tests/test_build_notifications.py backend/tests/test_failure_notifications_executor.py
git commit -m "refactor(builds): name the notification code for any build event

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Validate seven events

**Files:**
- Modify: `backend/app/services/pipeline_compiler.py` (module docstring; `NOTIFICATION_EVENTS`; `_notification_errors`; a new `_notification_event_errors`)
- Modify: `backend/tests/test_failure_notifications_yaml.py` (two expected messages)
- Test: `backend/tests/test_notification_events_yaml.py` (new)

**Interfaces:**
- Produces: `NOTIFICATION_EVENTS = ("on_start", "on_waiting", "on_success", "on_fixed", "on_failure", "on_cancelled", "on_complete")`.
- Produces: `_notification_event_errors(event: str, entries: Any, line_map: dict[int, int], block_line: int) -> list[PipelineError]` — the entry checks of one event. Messages start with `notifications.<event>[<index>]: `; a non-list or empty event gives `'notifications.<event>' must be a non-empty list of channel names or mappings`.
- Changed messages: an empty block gives `'notifications' requires at least one event (on_start, …, on_complete)`; an unknown key lists all seven after `allowed: `.
- Unchanged: `NOTIFICATION_ENTRY_FIELDS`, line numbers (block line for block and event errors, entry line for entry errors).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_notification_events_yaml.py`:

```python
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
```

Two messages the existing tests pin change wording. Update them:

**`backend/tests/test_failure_notifications_yaml.py`**. Replace:

````python
        ("notifications: {}\n", "'notifications' requires 'on_failure'"),
        (
            "notifications:\n  on_fail:\n    - deploy-alerts\n",
            "'notifications' has unknown key(s): on_fail (allowed: on_failure)",
        ),
````

with:

````python
        ("notifications: {}\n", "'notifications' requires at least one event (on_start, "),
        (
            "notifications:\n  on_fail:\n    - deploy-alerts\n",
            "'notifications' has unknown key(s): on_fail (allowed: on_start, on_waiting, "
            "on_success, on_fixed, on_failure, on_cancelled, on_complete)",
        ),
````

- [ ] **Step 2: Run the tests and see them fail**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events_yaml.py tests/test_failure_notifications_yaml.py
```

Expected: failures. The first is `test_the_seven_events_in_the_order_they_are_listed_to_authors`: `('on_failure',) == ('on_start', …)`.

- [ ] **Step 3: Accept every event**

The second replacement below ends in the middle of the old function: the rest of its body — everything after the `prefix = …` line — stays as it is and becomes the rest of `_notification_event_errors`.

**`backend/app/services/pipeline_compiler.py`** — edit 1 of 2. Replace:

````python
- notifications (top-level: who is told when a build fails)
````

with:

````python
- notifications (top-level: who is told about a build, and when)
````

**`backend/app/services/pipeline_compiler.py`** — edit 2 of 2. Replace:

````python
NOTIFICATION_EVENTS = ("on_failure",)
NOTIFICATION_ENTRY_FIELDS = ("channel", "message", "subject", "recipient")


def _notification_errors(
    value: Any, line_map: dict[int, int], top_line: int
) -> list[PipelineError]:
    """Validate the top-level ``notifications`` block (who is told when a
    build fails). ``build_notifications`` reads and sends it."""
    if not isinstance(value, dict):
        return [PipelineError(message="'notifications' must be a mapping", line=top_line)]

    block_line = line_map.get(id(value)) or top_line
    errors: list[PipelineError] = []

    unknown = sorted(str(k) for k in value if k not in NOTIFICATION_EVENTS)
    if unknown:
        errors.append(
            PipelineError(
                message=(
                    f"'notifications' has unknown key(s): {', '.join(unknown)} "
                    f"(allowed: {', '.join(NOTIFICATION_EVENTS)})"
                ),
                line=block_line,
            )
        )

    if "on_failure" not in value:
        if not unknown:
            errors.append(
                PipelineError(message="'notifications' requires 'on_failure'", line=block_line)
            )
        return errors

    entries = value["on_failure"]
    if not isinstance(entries, list) or not entries:
        errors.append(
            PipelineError(
                message=(
                    "'notifications.on_failure' must be a non-empty list of "
                    "channel names or mappings"
                ),
                line=block_line,
            )
        )
        return errors

    for index, entry in enumerate(entries):
        prefix = f"notifications.on_failure[{index}]"
````

with:

````python
# In the order they are listed to authors: by when they happen.
NOTIFICATION_EVENTS = (
    "on_start",
    "on_waiting",
    "on_success",
    "on_fixed",
    "on_failure",
    "on_cancelled",
    "on_complete",
)
NOTIFICATION_ENTRY_FIELDS = ("channel", "message", "subject", "recipient")


def _notification_errors(
    value: Any, line_map: dict[int, int], top_line: int
) -> list[PipelineError]:
    """Validate the top-level ``notifications`` block (who is told about a
    build, and when). ``build_notifications`` reads and sends it."""
    if not isinstance(value, dict):
        return [PipelineError(message="'notifications' must be a mapping", line=top_line)]

    block_line = line_map.get(id(value)) or top_line
    allowed = ", ".join(NOTIFICATION_EVENTS)
    errors: list[PipelineError] = []

    unknown = sorted(str(k) for k in value if k not in NOTIFICATION_EVENTS)
    if unknown:
        errors.append(
            PipelineError(
                message=(
                    f"'notifications' has unknown key(s): {', '.join(unknown)} "
                    f"(allowed: {allowed})"
                ),
                line=block_line,
            )
        )

    events = [event for event in NOTIFICATION_EVENTS if event in value]
    if not events and not unknown:
        errors.append(
            PipelineError(
                message=f"'notifications' requires at least one event ({allowed})",
                line=block_line,
            )
        )
    for event in events:
        errors.extend(_notification_event_errors(event, value[event], line_map, block_line))
    return errors


def _notification_event_errors(
    event: str, entries: Any, line_map: dict[int, int], block_line: int
) -> list[PipelineError]:
    """Validate the entries of one event of the ``notifications`` block."""
    errors: list[PipelineError] = []

    if not isinstance(entries, list) or not entries:
        errors.append(
            PipelineError(
                message=(
                    f"'notifications.{event}' must be a non-empty list of "
                    "channel names or mappings"
                ),
                line=block_line,
            )
        )
        return errors

    for index, entry in enumerate(entries):
        prefix = f"notifications.{event}[{index}]"
````

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events_yaml.py tests/test_failure_notifications_yaml.py
```

Expected: `64 passed` (40 of them in the new file).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pipeline_compiler.py backend/tests/test_notification_events_yaml.py backend/tests/test_failure_notifications_yaml.py
git commit -m "feat(pipeline): validate seven notification events

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Read, choose, word and send every event

**Files:**
- Rewrite: `backend/app/services/build_notifications.py`
- Modify: `backend/tests/test_build_notifications.py`, `backend/tests/test_failure_notifications_docs.py` (call sites)
- Test: `backend/tests/test_notification_events.py` (new)

**Interfaces:**
- Consumes: `NOTIFICATION_EVENTS` (Task 2); the names of Task 1.
- Produces: `Notification(channel, message=None, subject=None, recipient=None)` — unchanged shape.
- Produces: `BuildSnapshot` — the old fields, plus `status: str = "failed"`, `branch: str | None = None`, `waiting_stage: str = ""`, `waiting_step: str = ""`, `log_step_id: uuid.UUID | None = None` (the step under which problems are logged).
- Produces: `BuildSnapshot.capture(build, secrets, env_vars, builtins, *, status: str | None = None, waiting: tuple[Stage, Step] | None = None)`. `status` defaults to the build's. The failed step is looked for only when the status is `failed`. `log_step_id` is the waiting step, else the failed step, else the first step for a running build, else the last step with a `started_at`.
- Produces: `notifications_by_event(yaml_content: str | None) -> dict[str, list[Notification]]` — only events that have usable entries, in `NOTIFICATION_EVENTS` order. Replaces `failure_notifications`. Never raises.
- Produces: `matching_events(status: str, *, waiting: bool = False, fixed: bool = False) -> tuple[str, ...]` — most specific first.
- Produces: `select_notifications(by_event, events) -> list[tuple[str, Notification]]` — `(event, entry)` pairs, one event per destination.
- Produces: `async previous_build_failed(db, snapshot) -> bool`.
- Produces: `default_subject(values, event)` and `default_message(values, event)` — the event is now required.
- Produces: `async send_build_notifications(db, snapshot, *, report=None) -> int` — sends what the snapshot's moment calls for.
- Report lines start with the event's label: `Start notification`, `Approval notification`, `Success notification`, `Fixed notification`, `Failure notification`, `Cancellation notification`, `Completion notification`. The three sentences are unchanged: `… not sent: channel '<name>' was not found.`, `… not sent: channel '<name>' is disabled.`, `… via channel '<name>' could not be delivered. An administrator can find the error in the notification delivery history.` and `… via channel '<name>' timed out and was not sent.`
- The executor still imports `BuildSnapshot` and `send_build_notifications` and keeps working unchanged until Task 4.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_notification_events.py`:

```python
"""Every notification event: which entries a build's moment calls for, what
they say, and that they are sent."""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from tests._failure_notifications import builtins_for, load_build, seed_build, seed_channel
from tests._rbac import build_inmemory_factory


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture
def providers(monkeypatch):
    """Replace the three network senders; record what each was asked to send."""
    calls = {"slack": [], "telegram": [], "email": []}

    async def fake_slack(config, message, recipient):
        calls["slack"].append({"message": message, "recipient": recipient})

    async def fake_telegram(config, message, recipient):
        calls["telegram"].append({"message": message, "recipient": recipient})

    def fake_email(config, to_email, subject, body):
        calls["email"].append({"to": to_email, "subject": subject, "message": body})

    monkeypatch.setattr("app.services.notification_service._send_slack", fake_slack)
    monkeypatch.setattr("app.services.notification_service._send_telegram", fake_telegram)
    monkeypatch.setattr("app.services.notification_service._send_email_sync", fake_email)
    return calls


def _yaml(block: str) -> str:
    return (
        "name: deploy-staging\n"
        "notifications:\n" + block +
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )


def N(channel, **fields):
    from app.services.build_notifications import Notification

    return Notification(channel=channel, **fields)


# ── reading the block ───────────────────────────────────────────────────

def test_reader_returns_the_entries_of_each_event_that_has_any():
    from app.services.build_notifications import notifications_by_event

    found = notifications_by_event(_yaml(
        "  on_complete:\n"
        "    - team-chat\n"
        "  on_start:\n"
        "    - team-chat\n"
        "    - channel: ops-email\n"
        "      recipient: oncall@example.com\n"
        "  on_done:\n"            # not an event
        "    - team-chat\n"
        "  on_fixed: nope\n"      # not a list
        "  on_waiting:\n"         # nothing usable in it
        "    - 42\n"
    ))

    assert found == {
        "on_start": [N("team-chat"), N("ops-email", recipient="oncall@example.com")],
        "on_complete": [N("team-chat")],
    }


@pytest.mark.parametrize("yaml_content", [None, "", "name: [unclosed\n", "- a list\n",
                                          "notifications: team-chat\n", "name: x\nstages: []\n"])
def test_reader_is_safe_on_missing_or_malformed_input(yaml_content):
    from app.services.build_notifications import notifications_by_event

    assert notifications_by_event(yaml_content) == {}


# ── which events a moment matches ───────────────────────────────────────

@pytest.mark.parametrize(
    "status, waiting, fixed, expected",
    [
        ("running", False, False, ("on_start",)),
        ("running", True, False, ("on_waiting",)),
        ("success", False, False, ("on_success", "on_complete")),
        ("success", False, True, ("on_fixed", "on_success", "on_complete")),
        ("failed", False, False, ("on_failure", "on_complete")),
        ("failed", False, True, ("on_failure", "on_complete")),
        ("cancelled", False, False, ("on_cancelled", "on_complete")),
        ("pending", False, False, ()),
    ],
)
def test_matching_events_most_specific_first(status, waiting, fixed, expected):
    from app.services.build_notifications import matching_events

    assert matching_events(status, waiting=waiting, fixed=fixed) == expected


# ── one message per destination ─────────────────────────────────────────

def _select(by_event, *events):
    from app.services.build_notifications import select_notifications

    return select_notifications(by_event, events)


def test_a_channel_under_two_matching_events_gets_the_more_specific_one():
    custom = N("team-chat", message="It broke")
    by_event = {"on_failure": [custom], "on_complete": [N("team-chat")]}

    assert _select(by_event, "on_failure", "on_complete") == [("on_failure", custom)]
    assert _select(by_event, "on_success", "on_complete") == [("on_complete", N("team-chat"))]


def test_fixed_wins_over_success_which_wins_over_complete():
    by_event = {
        "on_fixed": [N("team-chat")],
        "on_success": [N("team-chat"), N("deploys")],
        "on_complete": [N("team-chat"), N("deploys"), N("audit")],
    }

    assert _select(by_event, "on_fixed", "on_success", "on_complete") == [
        ("on_fixed", N("team-chat")), ("on_success", N("deploys")), ("on_complete", N("audit")),
    ]
    assert _select(by_event, "on_success", "on_complete") == [
        ("on_success", N("team-chat")), ("on_success", N("deploys")), ("on_complete", N("audit")),
    ]


def test_the_same_channel_with_another_recipient_is_another_destination():
    by_event = {
        "on_failure": [N("ops-email", recipient="oncall@example.com")],
        "on_complete": [N("ops-email", recipient="team@example.com"), N("ops-email"),
                        N("ops-email", recipient="oncall@example.com")],
    }

    assert _select(by_event, "on_failure", "on_complete") == [
        ("on_failure", N("ops-email", recipient="oncall@example.com")),
        ("on_complete", N("ops-email", recipient="team@example.com")),
        ("on_complete", N("ops-email")),
    ]


def test_a_destination_listed_twice_under_one_event_is_sent_twice():
    by_event = {"on_complete": [N("team-chat", message="one"), N("team-chat", message="two")]}

    assert _select(by_event, "on_success", "on_complete") == [
        ("on_complete", N("team-chat", message="one")),
        ("on_complete", N("team-chat", message="two")),
    ]


def test_events_that_do_not_match_are_never_used():
    by_event = {"on_fixed": [N("team-chat")], "on_start": [N("team-chat")],
                "on_failure": [N("team-chat")]}

    assert _select(by_event, "on_success", "on_complete") == []


# ── default wording ─────────────────────────────────────────────────────

def _values(status, **build):
    return {
        "build": {"number": "12", "branch": "main", "commit": "3f2a9c1d5e6f7a8b", "status": status,
                  "failed_stage": "", "failed_step": "", "waiting_stage": "", "waiting_step": "",
                  "url": "https://ci.example.com/builds/abc", **build},
        "pipeline": {"name": "deploy-production"},
        "project": {"name": "Shop"},
    }


@pytest.mark.parametrize(
    "event, status, subject",
    [
        ("on_start", "running", "Build #12 of deploy-production started"),
        ("on_waiting", "running", "Build #12 of deploy-production is waiting for approval"),
        ("on_success", "success", "Build #12 of deploy-production succeeded"),
        ("on_fixed", "success", "Build #12 of deploy-production is fixed"),
        ("on_failure", "failed", "Build #12 of deploy-production failed"),
        ("on_cancelled", "cancelled", "Build #12 of deploy-production was cancelled"),
        ("on_complete", "success", "Build #12 of deploy-production succeeded"),
        ("on_complete", "failed", "Build #12 of deploy-production failed"),
        ("on_complete", "cancelled", "Build #12 of deploy-production was cancelled"),
    ],
)
def test_default_subject_for_every_event(event, status, subject):
    from app.services.build_notifications import default_message, default_subject

    values = _values(status)
    assert default_subject(values, event) == subject
    assert default_message(values, event) == (
        f"{subject}\n"
        "Project: Shop | Branch: main | Commit: 3f2a9c1\n"
        "https://ci.example.com/builds/abc"
    )


def test_default_message_says_where_a_build_is_waiting():
    from app.services.build_notifications import default_message

    values = _values("running", waiting_stage="release", waiting_step="approve production")
    assert default_message(values, "on_waiting") == (
        "Build #12 of deploy-production is waiting for approval\n"
        "Project: Shop | Branch: main | Commit: 3f2a9c1\n"
        "Waiting at: stage \"release\", step \"approve production\"\n"
        "https://ci.example.com/builds/abc"
    )


def test_on_complete_for_a_failed_build_says_where_it_failed():
    from app.services.build_notifications import default_message

    values = _values("failed", failed_stage="deploy", failed_step="apply manifests")
    assert "Failed at: stage \"deploy\", step \"apply manifests\"" in default_message(values, "on_complete")


# ── the snapshot ────────────────────────────────────────────────────────

async def _two_step_build(sf, *, build_status, statuses, started, yaml_content=None):
    """A build whose single stage has two steps. Returns ids with 'steps'."""
    from app.models.build import Step

    # Project names are unique, and some tests seed more than one build.
    kwargs = {"project_name": f"Project {uuid.uuid4().hex[:8]}"}
    if yaml_content:
        kwargs["yaml_content"] = yaml_content
    async with sf() as db:
        ids = await seed_build(db, build_status=build_status, step_status=statuses[0], **kwargs)
        first = await db.get(Step, ids["step"])
        second = Step(id=uuid.uuid4(), stage_id=ids["stage"], name="smoke test", step_type="run",
                      status=statuses[1], sort_order=1)
        db.add(second)
        now = datetime.now(timezone.utc)
        first.started_at = now if started[0] else None
        second.started_at = now if started[1] else None
        await db.commit()
    return {**ids, "steps": [ids["step"], second.id]}


async def _capture(sf, build_id, **kwargs):
    from app.services.build_notifications import BuildSnapshot

    async with sf() as db:
        build = await load_build(db, build_id)
        if kwargs.pop("waiting_at_second_step", False):
            stage = build.stages[0]
            kwargs["waiting"] = (stage, sorted(stage.steps, key=lambda s: s.sort_order)[1])
        return BuildSnapshot.capture(build, {}, {}, builtins_for(build), **kwargs)


async def test_snapshot_of_a_build_that_just_started(sf):
    ids = await _two_step_build(sf, build_status="running", statuses=("pending", "pending"),
                                started=(False, False))

    snapshot = await _capture(sf, ids["build"])

    assert snapshot.status == "running" and snapshot.branch == "develop"
    assert (snapshot.failed_stage, snapshot.failed_step, snapshot.failed_step_id) == ("", "", None)
    assert (snapshot.waiting_stage, snapshot.waiting_step) == ("", "")
    assert snapshot.log_step_id == ids["steps"][0], "problems are logged under the first step"


async def test_snapshot_of_a_build_waiting_for_approval(sf):
    ids = await _two_step_build(sf, build_status="running", statuses=("success", "running"),
                                started=(True, True))

    snapshot = await _capture(sf, ids["build"], waiting_at_second_step=True)

    assert snapshot.status == "running"
    assert (snapshot.waiting_stage, snapshot.waiting_step) == ("deploy", "smoke test")
    assert snapshot.log_step_id == ids["steps"][1]


async def test_snapshot_of_a_finished_build_logs_under_the_last_step_that_ran(sf):
    done = await _two_step_build(sf, build_status="success", statuses=("success", "success"),
                                 started=(True, True))
    cancelled = await _two_step_build(sf, build_status="cancelled",
                                      statuses=("success", "cancelled"), started=(True, False))

    assert (await _capture(sf, done["build"])).log_step_id == done["steps"][1]
    snapshot = await _capture(sf, cancelled["build"])
    assert snapshot.status == "cancelled" and snapshot.log_step_id == cancelled["steps"][0]


async def test_snapshot_of_a_failed_build_logs_under_the_failed_step(sf):
    ids = await _two_step_build(sf, build_status="failed", statuses=("failed", "pending"),
                                started=(True, False))

    snapshot = await _capture(sf, ids["build"])

    assert snapshot.status == "failed"
    assert (snapshot.failed_stage, snapshot.failed_step) == ("deploy", "apply manifests")
    assert snapshot.failed_step_id == snapshot.log_step_id == ids["steps"][0]


async def test_snapshot_status_can_be_given_and_a_missing_branch_is_none(sf):
    async with sf() as db:
        ids = await seed_build(db, build_status="pending", step_status="pending", branch=None)
        await db.commit()

    snapshot = await _capture(sf, ids["build"], status="running")

    assert snapshot.status == "running" and snapshot.branch is None


# ── "fixed" ─────────────────────────────────────────────────────────────

async def _history(sf, *earlier, branch="main"):
    """A pipeline whose build #428 is on *branch*, with earlier builds given
    as (number, status, branch). Returns a snapshot of #428."""
    from app.models.build import Build

    async with sf() as db:
        ids = await seed_build(db, build_status="success", step_status="success", branch=branch,
                               project_name=f"Project {uuid.uuid4().hex[:8]}")
        for number, status, earlier_branch in earlier:
            db.add(Build(id=uuid.uuid4(), pipeline_id=ids["pipeline"], number=number,
                         status=status, trigger_type="manual", branch=earlier_branch))
        await db.commit()
    return await _capture(sf, ids["build"])


async def _was_fixed(sf, *earlier, branch="main"):
    from app.services.build_notifications import previous_build_failed

    snapshot = await _history(sf, *earlier, branch=branch)
    async with sf() as db:
        return await previous_build_failed(db, snapshot)


async def test_success_after_a_failure_is_a_fix(sf):
    assert await _was_fixed(sf, (426, "success", "main"), (427, "failed", "main")) is True


async def test_success_after_a_success_is_not_a_fix(sf):
    assert await _was_fixed(sf, (426, "failed", "main"), (427, "success", "main")) is False


async def test_a_failure_on_another_branch_does_not_count(sf):
    assert await _was_fixed(sf, (426, "success", "main"), (427, "failed", "feature/x")) is False


async def test_cancelled_and_unfinished_builds_in_between_are_skipped(sf):
    assert await _was_fixed(
        sf, (424, "failed", "main"), (425, "cancelled", "main"), (426, "running", "main"),
        (427, "pending", "main"),
    ) is True


async def test_the_first_build_of_a_pipeline_or_branch_is_not_a_fix(sf):
    assert await _was_fixed(sf) is False
    assert await _was_fixed(sf, (427, "failed", "main"), branch="release") is False


async def test_builds_without_a_branch_are_one_branch(sf):
    assert await _was_fixed(sf, (426, "failed", None), (427, "failed", "main"), branch=None) is True
    assert await _was_fixed(sf, (427, "failed", ""), branch=None) is True
    assert await _was_fixed(sf, (427, "failed", "main"), branch=None) is False


async def test_a_later_build_number_is_not_a_previous_build(sf):
    assert await _was_fixed(sf, (429, "failed", "main")) is False


async def test_another_pipelines_failure_does_not_count(sf):
    from app.services.build_notifications import previous_build_failed

    async with sf() as db:
        other = await seed_build(db, build_status="failed", branch="main", project_name="Other")
        other_build = await load_build(db, other["build"])
        other_build.number = 427
        await db.commit()
    snapshot = await _history(sf)

    async with sf() as db:
        assert await previous_build_failed(db, snapshot) is False


# ── sending ─────────────────────────────────────────────────────────────

async def _seed(sf, block, *, build_status, channels=("team-chat",), **kwargs):
    step_status = {"running": "running", "cancelled": "cancelled"}.get(build_status, build_status)
    async with sf() as db:
        for name in channels:
            kind = "email" if "email" in name else "telegram" if "telegram" in name else "slack"
            await seed_channel(db, name, kind)
        ids = await seed_build(db, yaml_content=_yaml(block), build_status=build_status,
                               step_status=step_status, **kwargs)
        await db.commit()
    return ids


async def _send(sf, build_id, **capture):
    from app.services.build_notifications import send_build_notifications

    reports = []

    async def report(text):
        reports.append(text)

    snapshot = await _capture(sf, build_id, **capture)
    async with sf() as db:
        sent = await send_build_notifications(db, snapshot, report=report)
    return sent, reports


async def test_on_start_is_sent_for_a_build_that_started(sf, providers):
    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_complete:\n    - team-chat\n",
                      build_status="running")

    sent, reports = await _send(sf, ids["build"])

    assert (sent, reports) == (1, [])
    assert providers["slack"][0]["message"].splitlines()[0] == "Build #428 of deploy-staging started"


async def test_on_waiting_is_sent_with_the_step_that_needs_approval(sf, providers):
    block = (
        "  on_start:\n    - team-chat\n"
        "  on_waiting:\n"
        "    - team-chat\n"
        "    - channel: team-chat\n"
        "      message: \"${{ build.status }} at ${{ build.waiting_stage }} / "
        "${{ build.waiting_step }} [${{ build.failed_step }}]\"\n"
    )
    ids = await _two_step_build(sf, build_status="running", statuses=("success", "running"),
                                started=(True, True), yaml_content=_yaml(block))
    async with sf() as db:
        await seed_channel(db, "team-chat")
        await db.commit()

    sent, _ = await _send(sf, ids["build"], waiting_at_second_step=True)

    assert sent == 2
    default, custom = [call["message"] for call in providers["slack"]]
    assert default.splitlines()[0] == "Build #428 of deploy-staging is waiting for approval"
    assert "Waiting at: stage \"deploy\", step \"smoke test\"" in default
    assert custom == "running at deploy / smoke test []"


@pytest.mark.parametrize(
    "build_status, event, ending",
    [("success", "on_success", "succeeded"), ("cancelled", "on_cancelled", "was cancelled"),
     ("failed", "on_failure", "failed")],
)
async def test_each_outcome_sends_its_own_event(sf, providers, build_status, event, ending):
    others = "".join(f"  {e}:\n    - other-chat\n"
                     for e in ("on_success", "on_cancelled", "on_failure") if e != event)
    ids = await _seed(sf, f"  {event}:\n    - team-chat\n" + others, build_status=build_status,
                      channels=("team-chat", "other-chat"))

    sent, _ = await _send(sf, ids["build"])

    assert sent == 1
    assert providers["slack"][0]["message"].splitlines()[0] == f"Build #428 of deploy-staging {ending}"


@pytest.mark.parametrize("build_status", ["success", "failed", "cancelled"])
async def test_on_complete_is_sent_for_every_outcome_with_the_status(sf, providers, build_status):
    block = (
        "  on_complete:\n"
        "    - channel: team-chat\n"
        "      message: \"${{ pipeline.name }} #${{ build.number }}: ${{ build.status }}\"\n"
    )
    ids = await _seed(sf, block, build_status=build_status)

    await _send(sf, ids["build"])

    assert [call["message"] for call in providers["slack"]] == [f"deploy-staging #428: {build_status}"]


async def test_end_events_are_not_sent_while_the_build_is_running(sf, providers):
    block = "".join(f"  {event}:\n    - team-chat\n" for event in
                    ("on_success", "on_fixed", "on_failure", "on_cancelled", "on_complete"))
    ids = await _seed(sf, block, build_status="running")

    assert await _send(sf, ids["build"]) == (0, [])
    assert providers["slack"] == []


async def test_start_and_waiting_are_not_sent_when_the_build_ends(sf, providers):
    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_waiting:\n    - team-chat\n",
                      build_status="success")

    assert await _send(sf, ids["build"]) == (0, [])


OVERLAP = (
    "  on_failure:\n"
    "    - channel: team-chat\n"
    "      message: \"FIRE at ${{ build.failed_step }}\"\n"
    "  on_complete:\n"
    "    - team-chat\n"
)


async def test_a_failed_build_sends_one_message_to_a_channel_under_two_events(sf, providers):
    ids = await _seed(sf, OVERLAP, build_status="failed")

    sent, _ = await _send(sf, ids["build"])

    assert sent == 1
    assert [call["message"] for call in providers["slack"]] == ["FIRE at apply manifests"]


async def test_the_general_event_is_used_when_the_specific_one_does_not_match(sf, providers):
    ids = await _seed(sf, OVERLAP, build_status="success")

    await _send(sf, ids["build"])

    assert len(providers["slack"]) == 1
    assert providers["slack"][0]["message"].startswith("Build #428 of deploy-staging succeeded")


FIXED = (
    "  on_fixed:\n    - team-chat\n"
    "  on_success:\n    - team-chat\n    - deploys\n"
    "  on_complete:\n    - team-chat\n"
)


async def _earlier_build(sf, pipeline_id, status):
    from app.models.build import Build

    async with sf() as db:
        db.add(Build(id=uuid.uuid4(), pipeline_id=pipeline_id, number=427, status=status,
                     trigger_type="manual", branch="develop"))
        await db.commit()


async def test_a_fix_sends_the_fixed_message_once_and_success_to_the_others(sf, providers):
    ids = await _seed(sf, FIXED, build_status="success", channels=("team-chat", "deploys"))
    await _earlier_build(sf, ids["pipeline"], "failed")

    sent, _ = await _send(sf, ids["build"])

    assert sent == 2
    assert [call["message"].splitlines()[0] for call in providers["slack"]] == [
        "Build #428 of deploy-staging is fixed", "Build #428 of deploy-staging succeeded",
    ]


async def test_an_ordinary_success_never_sends_the_fixed_message(sf, providers):
    ids = await _seed(sf, FIXED, build_status="success", channels=("team-chat", "deploys"))
    await _earlier_build(sf, ids["pipeline"], "success")

    await _send(sf, ids["build"])

    assert [call["message"].splitlines()[0] for call in providers["slack"]] == [
        "Build #428 of deploy-staging succeeded"] * 2


async def test_the_previous_build_is_not_looked_up_unless_on_fixed_is_listed(sf, providers, monkeypatch):
    from app.services import build_notifications

    async def never(db, snapshot):
        raise AssertionError("looked up the previous build")

    monkeypatch.setattr(build_notifications, "previous_build_failed", never)
    ids = await _seed(sf, "  on_success:\n    - team-chat\n", build_status="success")

    assert (await _send(sf, ids["build"]))[0] == 1


async def test_a_failed_build_never_looks_up_the_previous_one(sf, providers, monkeypatch):
    from app.services import build_notifications

    async def never(db, snapshot):
        raise AssertionError("looked up the previous build")

    monkeypatch.setattr(build_notifications, "previous_build_failed", never)
    ids = await _seed(sf, FIXED + "  on_failure:\n    - team-chat\n", build_status="failed")

    assert (await _send(sf, ids["build"]))[0] == 1


@pytest.mark.parametrize(
    "build_status, event, label",
    [
        ("running", "on_start", "Start notification"),
        ("success", "on_success", "Success notification"),
        ("cancelled", "on_cancelled", "Cancellation notification"),
        ("success", "on_complete", "Completion notification"),
    ],
)
async def test_problems_are_reported_under_the_name_of_the_event(sf, providers, build_status, event, label):
    async with sf() as db:
        await seed_channel(db, "off-chat", enabled=False)
        await db.commit()
    ids = await _seed(sf, f"  {event}:\n    - nope\n    - off-chat\n    - team-chat\n",
                      build_status=build_status)

    sent, reports = await _send(sf, ids["build"])

    assert sent == 1, "the good entry is still sent"
    assert reports == [
        f"{label} not sent: channel 'nope' was not found.",
        f"{label} not sent: channel 'off-chat' is disabled.",
    ]


async def test_a_fixed_build_reports_problems_as_a_fixed_notification(sf, providers):
    ids = await _seed(sf, "  on_fixed:\n    - nope\n", build_status="success")
    await _earlier_build(sf, ids["pipeline"], "failed")

    assert await _send(sf, ids["build"]) == (
        0, ["Fixed notification not sent: channel 'nope' was not found."])


async def test_a_waiting_build_reports_problems_as_an_approval_notification(sf, providers):
    ids = await _two_step_build(
        sf, build_status="running", statuses=("success", "running"), started=(True, True),
        yaml_content=_yaml("  on_waiting:\n    - nope\n"))

    assert await _send(sf, ids["build"], waiting_at_second_step=True) == (
        0, ["Approval notification not sent: channel 'nope' was not found."])


async def test_built_in_values_are_escaped_for_telegram_on_a_new_event(sf, providers):
    block = (
        "  on_success:\n"
        "    - telegram-chat\n"
        "    - channel: telegram-chat\n"
        "      message: \"<b>done</b> on ${{ build.branch }}\"\n"
    )
    ids = await _seed(sf, block, build_status="success", channels=("telegram-chat",),
                      branch="fix/<script>&co")

    await _send(sf, ids["build"])

    default, custom = [call["message"] for call in providers["telegram"]]
    assert "Branch: fix/&lt;script&gt;&amp;co" in default
    assert custom == "<b>done</b> on fix/&lt;script&gt;&amp;co"


async def test_an_email_entry_gets_the_events_default_subject(sf, providers):
    block = "  on_cancelled:\n    - channel: ops-email\n      recipient: oncall@example.com\n"
    ids = await _seed(sf, block, build_status="cancelled", channels=("ops-email",))

    await _send(sf, ids["build"])

    assert providers["email"][0]["to"] == "oncall@example.com"
    assert providers["email"][0]["subject"] == "Build #428 of deploy-staging was cancelled"


async def test_a_delivery_is_recorded_for_a_new_event(sf, providers):
    from sqlalchemy import select

    from app.models.notification import NotificationDelivery

    ids = await _seed(sf, "  on_start:\n    - team-chat\n", build_status="running")

    await _send(sf, ids["build"])

    async with sf() as db:
        row = (await db.execute(select(NotificationDelivery))).scalar_one()
    assert row.build_id == ids["build"] and row.status == "sent"
```

The existing tests call three things whose signature changes. Update those call sites:

**`backend/tests/test_build_notifications.py`** — edit 1 of 7. Replace:

````python
async def _send(sf, build_id, **kwargs):
````

with:

````python
def failure_notifications(yaml_content):
    """The `on_failure` entries of a pipeline's YAML."""
    from app.services.build_notifications import notifications_by_event

    return notifications_by_event(yaml_content).get("on_failure", [])


async def _send(sf, build_id, **kwargs):
````

**`backend/tests/test_build_notifications.py`** — edit 2 of 7. Replace:

````python
    from app.services.build_notifications import Notification, failure_notifications
````

with:

````python
    from app.services.build_notifications import Notification
````

**`backend/tests/test_build_notifications.py`** — edit 3 of 7. Replace:

````python
    from app.services.build_notifications import failure_notifications

    assert failure_notifications(yaml_content) == []
````

with:

````python
    assert failure_notifications(yaml_content) == []
````

**`backend/tests/test_build_notifications.py`** — edit 4 of 7. Replace:

````python
    from app.services.build_notifications import failure_notifications

    entries = failure_notifications(_yaml(
        "    - 42\n"
````

with:

````python
    entries = failure_notifications(_yaml(
        "    - 42\n"
````

**`backend/tests/test_build_notifications.py`** — edit 5 of 7. Replace:

````python
    assert default_subject(VALUES) == "Build #428 of deploy-staging-inbox failed"
    assert default_message(VALUES) == (
````

with:

````python
    assert default_subject(VALUES, "on_failure") == "Build #428 of deploy-staging-inbox failed"
    assert default_message(VALUES, "on_failure") == (
````

**`backend/tests/test_build_notifications.py`** — edit 6 of 7. Replace:

````python
    assert default_message(values) == (
        "Build #7 of nightly failed\n"
````

with:

````python
    assert default_message(values, "on_failure") == (
        "Build #7 of nightly failed\n"
````

**`backend/tests/test_build_notifications.py`** — edit 7 of 7. Replace:

````python
        "failed_step_id", "secrets", "env_vars", "builtins",
    }
````

with:

````python
        "failed_step_id", "secrets", "env_vars", "builtins",
        "status", "branch", "waiting_stage", "waiting_step", "log_step_id",
    }
````

**`backend/tests/test_failure_notifications_docs.py`**. Replace:

````python
    from app.services.build_notifications import failure_notifications

    entries = failure_notifications(examples[0])
````

with:

````python
    from app.services.build_notifications import notifications_by_event

    entries = notifications_by_event(examples[0])["on_failure"]
````

- [ ] **Step 2: Run the tests and see them fail**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events.py tests/test_build_notifications.py tests/test_failure_notifications_docs.py
```

Expected: `80 failed, 28 passed`. The failures are `ImportError: cannot import name 'notifications_by_event'` (and the other new names) and `TypeError` for the `default_subject` / `default_message` calls that now pass an event.

- [ ] **Step 3: Rewrite the module**

Replace the whole content of `backend/app/services/build_notifications.py` with:

```python
"""Build notifications declared in a pipeline's YAML.

A pipeline can name the notification channels that are told about its builds::

    notifications:
      on_start:
        - team-chat
      on_failure:
        - channel: ops-email
          recipient: oncall@example.com
          subject: "Deploy failed"
          message: "Build #${{ build.number }} failed: ${{ build.url }}"
      on_complete:
        - team-chat

The server sends these itself: ``on_start`` and ``on_waiting`` while the build
runs, the others after it has ended and its agent has been released. The YAML
rules live in ``pipeline_compiler``; this module reads the block, decides which
entries a moment of a build calls for, and sends the messages.

Everything here works on plain values, never on ORM objects held by the
build executor: sending can roll a session back, and a rollback expires every
object loaded in it.
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.build import Build, Stage, Step
from app.models.notification import NotificationChannel
from app.models.pipeline import Pipeline
from app.services.notification_service import send_notification
from app.services.pipeline_compiler import NOTIFICATION_EVENTS
from app.services.step_actions.interpolation import interpolate_value

logger = logging.getLogger(__name__)

Report = Callable[[str], Awaitable[None]]
Values = dict[str, dict[str, str]]

# How long one entry may take. A stalled provider must not hold a build
# worker; the entry is abandoned and the next one is tried.
SEND_TIMEOUT_SECONDS: float = 30

# Channel types whose message format gives <, > and & a meaning: Telegram
# parses the text as HTML, Slack reads <...> as links and mentions. Values
# substituted into a message are escaped for these; text the author wrote is
# left alone. Email is escaped by its sender, for the HTML part only.
_ESCAPED_CHANNEL_TYPES = {"telegram", "slack"}

# The event that describes how a build ended; ``on_complete`` borrows its words.
_OUTCOME_EVENT = {"success": "on_success", "failed": "on_failure", "cancelled": "on_cancelled"}

# event -> (how the subject ends, what the build log calls the notification)
_WORDING = {
    "on_start": ("started", "Start notification"),
    "on_waiting": ("is waiting for approval", "Approval notification"),
    "on_success": ("succeeded", "Success notification"),
    "on_fixed": ("is fixed", "Fixed notification"),
    "on_failure": ("failed", "Failure notification"),
    "on_cancelled": ("was cancelled", "Cancellation notification"),
    "on_complete": ("", "Completion notification"),
}


@dataclass(frozen=True)
class Notification:
    """One entry of an event, in normalized form."""

    channel: str
    message: str | None = None
    subject: str | None = None
    recipient: str | None = None


@dataclass(frozen=True)
class BuildSnapshot:
    """A build at the moment something worth telling happened, as plain values.

    ``status`` is ``running`` for a build that has just started or is waiting
    for approval (``waiting_step`` says which), otherwise how it ended.
    """

    build_id: uuid.UUID
    pipeline_id: uuid.UUID
    number: int
    failed_stage: str
    failed_step: str
    failed_step_id: uuid.UUID | None
    secrets: dict[str, str]
    env_vars: dict[str, str]
    builtins: Values
    status: str = "failed"
    branch: str | None = None
    waiting_stage: str = ""
    waiting_step: str = ""
    # The step under which problems with a notification are logged.
    log_step_id: uuid.UUID | None = None

    @classmethod
    def capture(
        cls,
        build: Build,
        secrets: dict[str, str],
        env_vars: dict[str, str],
        builtins: Values,
        *,
        status: str | None = None,
        waiting: tuple[Stage, Step] | None = None,
    ) -> BuildSnapshot:
        """Snapshot *build* while its session is still usable. Expects
        ``build.stages`` and their steps to be loaded.

        *status* defaults to the build's own. Pass *waiting* (stage, step)
        when the build is about to wait for approval at that step.
        """
        status = status or build.status
        stage_name, step_name, failed_step = ("", "", None)
        if status == "failed":
            stage_name, step_name, failed_step = failed_step_of(build)

        steps = [
            step
            for stage in sorted(build.stages, key=lambda s: s.sort_order)
            for step in sorted(stage.steps, key=lambda s: s.sort_order)
        ]
        if waiting is not None:
            log_step = waiting[1]
        elif failed_step is not None:
            log_step = failed_step
        elif status == "running":
            log_step = steps[0] if steps else None
        else:
            ran = [step for step in steps if step.started_at is not None]
            log_step = ran[-1] if ran else None

        return cls(
            build_id=build.id,
            pipeline_id=build.pipeline_id,
            number=build.number,
            failed_stage=stage_name,
            failed_step=step_name,
            failed_step_id=failed_step.id if failed_step is not None else None,
            secrets=dict(secrets),
            env_vars=dict(env_vars),
            builtins={namespace: dict(values) for namespace, values in builtins.items()},
            status=status,
            branch=build.branch or None,
            waiting_stage=waiting[0].name if waiting is not None else "",
            waiting_step=waiting[1].name if waiting is not None else "",
            log_step_id=log_step.id if log_step is not None else None,
        )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _entries(value: Any) -> list[Notification]:
    if not isinstance(value, list):
        return []
    result: list[Notification] = []
    for entry in value:
        if isinstance(entry, str):
            if entry.strip():
                result.append(Notification(channel=entry.strip()))
        elif isinstance(entry, dict):
            channel = _text(entry.get("channel"))
            if channel:
                result.append(
                    Notification(
                        channel=channel.strip(),
                        message=_text(entry.get("message")),
                        subject=_text(entry.get("subject")),
                        recipient=_text(entry.get("recipient")),
                    )
                )
    return result


def notifications_by_event(yaml_content: str | None) -> dict[str, list[Notification]]:
    """Read the ``notifications`` block from pipeline YAML: the entries of
    each event that has any.

    Never raises. Missing, unparseable or malformed input yields an empty
    dict, and malformed entries are skipped: the validator is what reports
    mistakes to the author, so this reader only has to be safe.
    """
    try:
        data = yaml.safe_load(yaml_content or "")
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("notifications")
    if not isinstance(block, dict):
        return {}
    found = {event: _entries(block.get(event)) for event in NOTIFICATION_EVENTS}
    return {event: entries for event, entries in found.items() if entries}


def matching_events(status: str, *, waiting: bool = False, fixed: bool = False) -> tuple[str, ...]:
    """The events a moment of a build matches, most specific first."""
    if status == "running":
        return ("on_waiting",) if waiting else ("on_start",)
    if status == "success":
        return (("on_fixed",) if fixed else ()) + ("on_success", "on_complete")
    if status == "failed":
        return ("on_failure", "on_complete")
    if status == "cancelled":
        return ("on_cancelled", "on_complete")
    return ()


def select_notifications(
    by_event: dict[str, list[Notification]], events: tuple[str, ...]
) -> list[tuple[str, Notification]]:
    """The entries to send for *events* (most specific first), each with the
    event it comes from.

    A destination — a channel and a recipient — gets the entries of the first
    event that lists it and is skipped in the later, more general ones.
    Entries within one event are never dropped.
    """
    chosen: list[tuple[str, Notification]] = []
    taken: set[tuple[str, str | None]] = set()
    for event in events:
        entries = [
            entry for entry in by_event.get(event, []) if (entry.channel, entry.recipient) not in taken
        ]
        chosen.extend((event, entry) for entry in entries)
        taken.update((entry.channel, entry.recipient) for entry in entries)
    return chosen


def failed_step_of(build: Build) -> tuple[str, str, Step | None]:
    """The first failed step of *build* as ``(stage name, step name, step)``,
    or ``("", "", None)``. Expects ``build.stages`` and their steps loaded."""
    for stage in sorted(build.stages, key=lambda s: s.sort_order):
        for step in sorted(stage.steps, key=lambda s: s.sort_order):
            if step.status == "failed":
                return stage.name, step.name, step
    return "", "", None


async def previous_build_failed(db: AsyncSession, snapshot: BuildSnapshot) -> bool:
    """Whether the build before *snapshot* — same pipeline, same branch, and
    ended as success or failed — was a failure. Cancelled and unfinished
    builds are skipped."""
    same_branch = (
        Build.branch == snapshot.branch
        if snapshot.branch
        else or_(Build.branch.is_(None), Build.branch == "")
    )
    previous = await db.scalar(
        select(Build.status)
        .where(
            Build.pipeline_id == snapshot.pipeline_id,
            Build.number < snapshot.number,
            Build.status.in_(("success", "failed")),
            same_branch,
        )
        .order_by(Build.number.desc())
        .limit(1)
    )
    return previous == "failed"


def build_url(build_id: Any) -> str:
    base = get_settings().MEGOOCI_PUBLIC_URL.rstrip("/")
    return f"{base}/builds/{build_id}"


def _outcome_event(event: str, values: Values) -> str:
    """*event*, or for ``on_complete`` the event that describes the outcome."""
    if event != "on_complete":
        return event
    return _OUTCOME_EVENT.get(values.get("build", {}).get("status", ""), "on_success")


def default_subject(values: Values, event: str) -> str:
    build = values.get("build", {})
    pipeline = values.get("pipeline", {})
    ending = _WORDING[_outcome_event(event, values)][0]
    return f"Build #{build.get('number', '')} of {pipeline.get('name', '')} {ending}"


def default_message(values: Values, event: str) -> str:
    """The message used when an entry does not write its own."""
    build = values.get("build", {})
    lines = [default_subject(values, event)]

    details = []
    project_name = values.get("project", {}).get("name")
    if project_name:
        details.append(f"Project: {project_name}")
    if build.get("branch"):
        details.append(f"Branch: {build['branch']}")
    if build.get("commit"):
        details.append(f"Commit: {build['commit'][:7]}")
    if details:
        lines.append(" | ".join(details))

    if build.get("failed_step"):
        lines.append(
            f"Failed at: stage \"{build.get('failed_stage', '')}\", "
            f"step \"{build['failed_step']}\""
        )
    if build.get("waiting_step"):
        lines.append(
            f"Waiting at: stage \"{build.get('waiting_stage', '')}\", "
            f"step \"{build['waiting_step']}\""
        )
    if build.get("url"):
        lines.append(build["url"])
    return "\n".join(lines)


def _escaped(values: Values) -> Values:
    return {
        namespace: {key: html.escape(value, quote=False) for key, value in entries.items()}
        for namespace, entries in values.items()
    }


async def send_build_notifications(
    db: AsyncSession,
    snapshot: BuildSnapshot,
    *,
    report: Report | None = None,
) -> int:
    """Send the notifications the moment in *snapshot* calls for.

    Reads the block from the pipeline's YAML as stored now. Entries are
    independent: a problem with one is passed to *report* (one short line,
    safe to show in the build log) and the rest still go out. Returns the
    number of messages sent.
    """

    async def _report(text: str) -> None:
        try:
            if report is not None:
                await report(text)
            else:
                logger.warning("build %s: %s", snapshot.build_id, text)
        except Exception:
            logger.exception("build %s: could not report: %s", snapshot.build_id, text)

    pipeline = (
        await db.execute(
            select(Pipeline.name, Pipeline.yaml_content).where(Pipeline.id == snapshot.pipeline_id)
        )
    ).first()
    if pipeline is None:
        return 0
    pipeline_name, yaml_content = pipeline
    by_event = notifications_by_event(yaml_content)
    if not by_event:
        return 0

    fixed = (
        snapshot.status == "success"
        and "on_fixed" in by_event
        and await previous_build_failed(db, snapshot)
    )
    events = matching_events(snapshot.status, waiting=bool(snapshot.waiting_step), fixed=fixed)
    chosen = select_notifications(by_event, events)
    if not chosen:
        return 0

    values: Values = {namespace: dict(entries_) for namespace, entries_ in snapshot.builtins.items()}
    values.setdefault("pipeline", {}).setdefault("name", pipeline_name)
    values["build"] = {
        **values.get("build", {}),
        "number": str(snapshot.number),
        "status": snapshot.status,
        "failed_stage": snapshot.failed_stage,
        "failed_step": snapshot.failed_step,
        "waiting_stage": snapshot.waiting_stage,
        "waiting_step": snapshot.waiting_step,
        "url": build_url(snapshot.build_id),
    }
    escaped = _escaped(values)

    sent = 0
    for event, entry in chosen:
        label = _WORDING[event][1]
        channel = (
            await db.execute(
                select(
                    NotificationChannel.id,
                    NotificationChannel.enabled,
                    NotificationChannel.channel_type,
                ).where(NotificationChannel.name == entry.channel)
            )
        ).first()
        if channel is None:
            await _report(f"{label} not sent: channel '{entry.channel}' was not found.")
            continue
        channel_id, enabled, channel_type = channel
        if not enabled:
            await _report(f"{label} not sent: channel '{entry.channel}' is disabled.")
            continue

        shown = escaped if channel_type in _ESCAPED_CHANNEL_TYPES else values
        message = (
            interpolate_value(entry.message, snapshot.secrets, snapshot.env_vars, shown)
            if entry.message
            else default_message(shown, event)
        )
        subject = (
            interpolate_value(entry.subject, snapshot.secrets, snapshot.env_vars, values)
            if entry.subject
            else default_subject(values, event)
        )

        try:
            delivery = await asyncio.wait_for(
                send_notification(
                    db,
                    channel_id,
                    message,
                    subject=subject,
                    recipient=entry.recipient,
                    build_id=snapshot.build_id,
                ),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await _rollback(db)
            await _report(
                f"{label} via channel '{entry.channel}' timed out and was not sent."
            )
            continue
        except Exception:
            logger.exception(
                "build %s: %s via %s", snapshot.build_id, label.lower(), entry.channel
            )
            await _rollback(db)
            delivered = False
        else:
            delivered = delivery.status == "sent"

        if delivered:
            sent += 1
        else:
            # The provider's error text is deliberately left out: it can quote
            # the channel's webhook URL or bot token, and the build log is
            # readable by everyone who can see the build.
            await _report(
                f"{label} via channel '{entry.channel}' could not be "
                "delivered. An administrator can find the error in the notification "
                "delivery history."
            )
    return sent


async def _rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        logger.exception("could not roll back after a failed notification")
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events.py tests/test_build_notifications.py tests/test_failure_notifications_docs.py
```

Expected: `108 passed` (69 of them in the new file).

- [ ] **Step 5: Run the whole backend suite**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `687 passed`. The executor's failure-notification tests pass unchanged: a failed build's snapshot still sends `on_failure`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/build_notifications.py backend/tests/test_notification_events.py backend/tests/test_build_notifications.py backend/tests/test_failure_notifications_docs.py
git commit -m "feat(builds): choose and send notifications for every build event

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The executor's three moments

**Files:**
- Modify: `backend/app/services/build_executor.py`
- Modify: `backend/tests/test_failure_notifications_executor.py` (two tests that expected no snapshot for a build that did not fail)
- Test: `backend/tests/test_notification_events_executor.py` (new)

**Interfaces:**
- Consumes: `BuildSnapshot.capture(..., status=, waiting=)`, `notifications_by_event`, `send_build_notifications` (Task 3).
- Changes: `_run_build_stages` returns a `BuildSnapshot` for every build that ran (its `status` is `success`, `failed` or `cancelled`), and `None` only when the build did not run.
- Changes: `_capture_snapshot(build, secrets, env_vars, builtins, **moment) -> BuildSnapshot | None` passes `moment` to `BuildSnapshot.capture`. Never raises.
- Changes: `_send_notifications(snapshot, session_factory)` logs problems under `snapshot.log_step_id` and opens a Redis client only when there is a line to log. Never raises.
- Produces: `async _lists_event(session_factory, pipeline_id, event) -> bool` — on a session of its own; `False` on any error.
- Produces: `_send_in_background(snapshot | None, session_factory) -> None` and `async _wait_for_background_sends(build_id) -> None`; module state `_background_sends: dict[uuid.UUID, set[asyncio.Task]]`, emptied as tasks finish.

**Where the hooks go:**
- **Start:** in `_run_build_stages`, right after `_load_scope_context` — the build is already marked `running` — and only if the pipeline lists `on_start`.
- **Waiting:** in the step loop, after `step_started` is published and before `_execute_step`, for `step.step_type == "wait_input"`, and only if the pipeline lists `on_waiting`.
- **End:** where the failure snapshot is taken today, now for every outcome. `execute_build` sends it after releasing the agent and dispatching the next build, and after waiting for this build's background sends.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_notification_events_executor.py`:

```python
"""The executor tells the pipeline's channels when a build starts, waits for
approval and ends — without delaying the build or changing its result."""
import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._failure_notifications import FakeRedis, seed_build, seed_channel
from tests._rbac import build_inmemory_factory

FAILED = {"exit_code": 1, "status": "failed"}
OK = {"exit_code": 0, "status": "success"}


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture
def slack(monkeypatch):
    sent = []

    async def fake_slack(config, message, recipient):
        sent.append(message)

    monkeypatch.setattr("app.services.notification_service._send_slack", fake_slack)
    return sent


@pytest.fixture
def fake_redis(monkeypatch):
    """The notifier opens its own Redis client for a build-log line."""
    from app.services import build_executor

    redis = FakeRedis()
    monkeypatch.setattr(build_executor.aioredis, "from_url", lambda *a, **k: redis)
    return redis


def _yaml(block: str) -> str:
    return (
        "name: deploy-staging\n"
        "notifications:\n" + block +
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )


async def _seed(sf, block, *, step_type="run", build_status="pending"):
    from app.models.build import Step

    async with sf() as db:
        await seed_channel(db, "team-chat")
        ids = await seed_build(db, yaml_content=_yaml(block), build_status=build_status,
                               step_status="pending")
        (await db.get(Step, ids["step"])).step_type = step_type
        await db.commit()
    return ids


async def _run_stages(sf, monkeypatch, build_id, step_result=OK, *, on_step=None):
    """Run the real executor loop with the step itself faked, then wait for
    the sends it started. Returns the snapshot of the build as it ended."""
    from app.services import build_executor
    from app.services.step_actions.base import StepResult

    async def scope(db, build):
        return {}, {}, {
            "build": {"number": str(build.number), "branch": build.branch or "",
                      "commit": build.commit_sha or ""},
            "pipeline": {"name": "deploy-staging"}, "project": {"name": "Inbox Staging"},
        }

    async def fake_step(*, step, build, **kwargs):
        if on_step is not None:
            await on_step(build)
        return StepResult(**step_result)

    monkeypatch.setattr(build_executor, "_load_scope_context", scope)
    monkeypatch.setattr(build_executor, "_execute_step", fake_step)
    snapshot = await build_executor._run_build_stages(
        build_id=build_id, claimed_agent_id=uuid.uuid4(),
        session_factory=sf, redis_client=FakeRedis(), channel="build:x:logs",
    )
    await build_executor._wait_for_background_sends(build_id)
    return snapshot


def _first_lines(messages):
    return [message.splitlines()[0] for message in messages]


# ── on_start ────────────────────────────────────────────────────────────

async def test_on_start_is_sent_once_when_the_build_starts(sf, slack, monkeypatch):
    ids = await _seed(sf, "  on_start:\n    - team-chat\n")

    await _run_stages(sf, monkeypatch, ids["build"])

    assert _first_lines(slack) == ["Build #428 of deploy-staging started"]


async def test_the_first_step_does_not_wait_for_the_start_message(sf, monkeypatch):
    """A slow channel must not hold the build up."""
    ids = await _seed(sf, "  on_start:\n    - team-chat\n")
    release, order = asyncio.Event(), []

    async def slow_slack(config, message, recipient):
        order.append("send begins")
        await release.wait()
        order.append("send ends")

    async def step_runs(build):
        await asyncio.sleep(0.05)  # let the background send get going
        order.append("step runs")
        release.set()

    monkeypatch.setattr("app.services.notification_service._send_slack", slow_slack)

    await _run_stages(sf, monkeypatch, ids["build"], on_step=step_runs)

    assert order == ["send begins", "step runs", "send ends"]


async def test_nothing_is_started_for_a_pipeline_that_does_not_list_the_event(sf, slack, monkeypatch):
    from app.services import build_executor

    started = []
    monkeypatch.setattr(build_executor, "_send_in_background",
                        lambda snapshot, session_factory: started.append(snapshot))
    ids = await _seed(sf, "  on_failure:\n    - team-chat\n", step_type="wait_input")

    await _run_stages(sf, monkeypatch, ids["build"])

    assert started == []


async def test_the_start_snapshot_is_of_a_running_build(sf, monkeypatch):
    from app.services import build_executor

    started = []
    monkeypatch.setattr(build_executor, "_send_in_background",
                        lambda snapshot, session_factory: started.append(snapshot))
    ids = await _seed(sf, "  on_start:\n    - team-chat\n")

    ended = await _run_stages(sf, monkeypatch, ids["build"])

    assert [s.status for s in started] == ["running"]
    assert started[0].waiting_step == "" and started[0].log_step_id == ids["step"]
    assert ended.status == "success"


# ── on_waiting ──────────────────────────────────────────────────────────

async def test_on_waiting_is_sent_when_the_build_reaches_an_approval_step(sf, slack, monkeypatch):
    ids = await _seed(sf, "  on_waiting:\n    - team-chat\n", step_type="wait_input")

    await _run_stages(sf, monkeypatch, ids["build"])

    assert len(slack) == 1
    assert slack[0].splitlines()[0] == "Build #428 of deploy-staging is waiting for approval"
    assert "Waiting at: stage \"deploy\", step \"apply manifests\"" in slack[0]


@pytest.mark.parametrize("step_type", ["wait_webhook", "run", "notify"])
async def test_on_waiting_is_only_for_approval_steps(sf, slack, monkeypatch, step_type):
    ids = await _seed(sf, "  on_waiting:\n    - team-chat\n", step_type=step_type)

    await _run_stages(sf, monkeypatch, ids["build"])

    assert slack == []


async def test_the_approval_step_does_not_wait_for_the_message(sf, monkeypatch):
    ids = await _seed(sf, "  on_waiting:\n    - team-chat\n", step_type="wait_input")
    release, order = asyncio.Event(), []

    async def slow_slack(config, message, recipient):
        order.append("send begins")
        await release.wait()
        order.append("send ends")

    async def step_runs(build):
        await asyncio.sleep(0.05)
        order.append("step runs")
        release.set()

    monkeypatch.setattr("app.services.notification_service._send_slack", slow_slack)

    await _run_stages(sf, monkeypatch, ids["build"], on_step=step_runs)

    assert order == ["send begins", "step runs", "send ends"]


# ── the end of the build ────────────────────────────────────────────────

ALL_END = "".join(f"  {event}:\n    - channel: team-chat\n      message: \"{event}\"\n"
                  for event in ("on_success", "on_failure", "on_cancelled"))


@pytest.mark.parametrize("step_result, expected", [(OK, "on_success"), (FAILED, "on_failure")])
async def test_a_finished_build_sends_the_event_of_its_outcome(sf, slack, fake_redis, monkeypatch,
                                                               step_result, expected):
    from app.services import build_executor

    ids = await _seed(sf, ALL_END)
    snapshot = await _run_stages(sf, monkeypatch, ids["build"], step_result)

    await build_executor._send_notifications(snapshot, sf)

    assert slack == [expected]


async def test_a_build_cancelled_while_running_sends_on_cancelled(sf, slack, fake_redis, monkeypatch):
    from app.models.build import Build
    from app.services import build_executor

    ids = await _seed(sf, ALL_END + "  on_complete:\n    - team-chat\n")

    async def cancel(build):
        async with sf() as other:
            (await other.get(Build, build.id)).status = "cancelled"
            await other.commit()

    snapshot = await _run_stages(sf, monkeypatch, ids["build"], on_step=cancel)
    await build_executor._send_notifications(snapshot, sf)

    assert snapshot.status == "cancelled"
    assert slack == ["on_cancelled"], "and not on_complete as well: one message per channel"


async def test_start_and_end_of_one_build(sf, slack, fake_redis, monkeypatch):
    from app.services import build_executor

    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_complete:\n    - team-chat\n")

    snapshot = await _run_stages(sf, monkeypatch, ids["build"])
    await build_executor._send_notifications(snapshot, sf)

    assert _first_lines(slack) == [
        "Build #428 of deploy-staging started", "Build #428 of deploy-staging succeeded"]


async def test_the_block_is_read_when_the_event_happens_not_when_the_build_was_triggered(
        sf, slack, fake_redis, monkeypatch):
    """Someone edits the pipeline while a build of it is running."""
    from app.models.pipeline import Pipeline
    from app.services import build_executor

    ids = await _seed(sf, "  on_start:\n    - team-chat\n")

    async def edit_pipeline(build):
        async with sf() as other:
            pipeline = await other.get(Pipeline, ids["pipeline"])
            pipeline.yaml_content = _yaml("  on_success:\n    - team-chat\n")
            await other.commit()

    snapshot = await _run_stages(sf, monkeypatch, ids["build"], on_step=edit_pipeline)
    await build_executor._send_notifications(snapshot, sf)

    assert _first_lines(slack) == [
        "Build #428 of deploy-staging started", "Build #428 of deploy-staging succeeded"]


async def _agent(sf, build_id):
    from app.models.agent import Agent

    agent_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with sf() as db:
        db.add(Agent(id=agent_id, name="a1", status="online", enabled=True,
                     connected_at=now, last_seen_at=now, current_build_id=build_id))
        await db.commit()
    return agent_id


async def test_the_end_message_waits_for_a_start_message_still_being_sent(sf, fake_redis, monkeypatch):
    """"Started" must not arrive after "failed"."""
    from app.services import build_executor
    from app.services.build_notifications import BuildSnapshot

    ids = await _seed(sf, "  on_start:\n    - team-chat\n")
    agent_id = await _agent(sf, ids["build"])
    order = []

    def snapshot(status):
        return BuildSnapshot(
            build_id=ids["build"], pipeline_id=ids["pipeline"], number=428, failed_stage="",
            failed_step="", failed_step_id=None, secrets={}, env_vars={}, builtins={},
            status=status,
        )

    async def fake_send(snap, session_factory):
        order.append(f"{snap.status} begins")
        if snap.status == "running":
            await asyncio.sleep(0.1)
        order.append(f"{snap.status} ends")

    async def fake_stages(**kwargs):
        build_executor._send_in_background(snapshot("running"), sf)
        return snapshot("failed")

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(build_executor, "_send_notifications", fake_send)
    monkeypatch.setattr(build_executor, "_run_build_stages", fake_stages)
    monkeypatch.setattr(build_executor, "release_agent", noop)
    monkeypatch.setattr(build_executor, "send_build_finished", noop)
    monkeypatch.setattr(build_executor, "dispatch_pending_builds", noop)

    await build_executor.execute_build(ids["build"], session_factory=sf, claimed_agent_id=agent_id)

    assert order == ["running begins", "running ends", "failed begins", "failed ends"]
    assert ids["build"] not in build_executor._background_sends, "finished sends are forgotten"


@pytest.mark.parametrize("build_status", ["cancelled", "success", "failed", "running"])
async def test_a_build_that_is_not_pending_is_not_run_and_sends_nothing(sf, slack, fake_redis,
                                                                        monkeypatch, build_status):
    """For example a build cancelled, or replaced by a newer trigger, before it started."""
    from app.services import build_executor

    block = "".join(f"  {event}:\n    - team-chat\n"
                    for event in ("on_start", "on_cancelled", "on_complete"))
    ids = await _seed(sf, block, build_status=build_status)

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(build_executor, "release_agent", noop)

    await build_executor.execute_build(ids["build"], session_factory=sf, claimed_agent_id=uuid.uuid4())

    assert slack == []


# ── nothing here may disturb the build ──────────────────────────────────

async def test_a_send_that_raises_never_changes_the_build(sf, monkeypatch):
    from app.models.build import Build
    from app.services import build_executor

    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_waiting:\n    - team-chat\n",
                      step_type="wait_input")

    async def explode(*args, **kwargs):
        raise RuntimeError("notification subsystem is down")

    monkeypatch.setattr(build_executor, "send_build_notifications", explode)

    snapshot = await _run_stages(sf, monkeypatch, ids["build"])

    async with sf() as db:
        assert (await db.get(Build, ids["build"])).status == "success"
    assert snapshot.status == "success"


async def test_a_pipeline_whose_notifications_cannot_be_read_still_builds(sf, slack, monkeypatch):
    from app.models.build import Build
    from app.services import build_executor

    ids = await _seed(sf, "  on_start:\n    - team-chat\n", step_type="wait_input")

    def explode(yaml_content):
        raise RuntimeError("cannot parse")

    monkeypatch.setattr(build_executor, "notifications_by_event", explode)

    await _run_stages(sf, monkeypatch, ids["build"])

    async with sf() as db:
        assert (await db.get(Build, ids["build"])).status == "success"
    assert slack == []


async def test_a_snapshot_that_cannot_be_taken_sends_nothing_and_the_build_goes_on(sf, slack, monkeypatch):
    from app.models.build import Build
    from app.services import build_executor
    from app.services.build_notifications import BuildSnapshot

    ids = await _seed(sf, "  on_start:\n    - team-chat\n")

    def explode(*args, **kwargs):
        raise RuntimeError("no snapshot")

    monkeypatch.setattr(BuildSnapshot, "capture", explode)

    assert await _run_stages(sf, monkeypatch, ids["build"]) is None
    async with sf() as db:
        assert (await db.get(Build, ids["build"])).status == "success"
    assert slack == []


# ── where problems are logged ───────────────────────────────────────────

async def _system_lines(sf, step_id):
    from app.models.build import LogChunk

    async with sf() as db:
        return [c.content for c in (await db.execute(
            select(LogChunk).where(LogChunk.step_id == step_id, LogChunk.stream == "system")
        )).scalars()]


async def test_a_problem_with_a_start_notification_is_logged_under_the_first_step(
        sf, slack, fake_redis, monkeypatch):
    ids = await _seed(sf, "  on_start:\n    - no-such-channel\n    - team-chat\n")

    await _run_stages(sf, monkeypatch, ids["build"])

    lines = await _system_lines(sf, ids["step"])
    assert any("Start notification not sent: channel 'no-such-channel' was not found" in line
               for line in lines), lines
    assert len(slack) == 1, "the entry after the bad one is still sent"
    assert fake_redis.published, "the log line is also published live"


async def test_a_problem_with_an_end_notification_is_logged_under_the_last_step_that_ran(
        sf, slack, fake_redis, monkeypatch):
    from app.services import build_executor

    ids = await _seed(sf, "  on_success:\n    - no-such-channel\n")
    snapshot = await _run_stages(sf, monkeypatch, ids["build"])

    await build_executor._send_notifications(snapshot, sf)

    assert snapshot.log_step_id == ids["step"]
    lines = await _system_lines(sf, ids["step"])
    assert any("Success notification not sent: channel 'no-such-channel' was not found" in line
               for line in lines), lines


async def test_no_redis_client_is_opened_when_there_is_nothing_to_report(sf, slack, monkeypatch):
    from app.services import build_executor

    def no_redis(*args, **kwargs):
        raise AssertionError("opened a Redis client")

    monkeypatch.setattr(build_executor.aioredis, "from_url", no_redis)
    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_success:\n    - team-chat\n")

    snapshot = await _run_stages(sf, monkeypatch, ids["build"])
    await build_executor._send_notifications(snapshot, sf)

    assert len(slack) == 2
```

Two existing tests asserted that a successful or cancelled build hands back nothing. It now hands back a snapshot of how it ended. Update them:

**`backend/tests/test_failure_notifications_executor.py`** — edit 1 of 4. Replace:

````python
"""The executor notifies when, and only when, a build ends failed — after the
agent has been released, and without ever disturbing the build's result."""
````

with:

````python
"""The executor notifies about a failed build after the agent has been
released, and without ever disturbing the build's result."""
````

**`backend/tests/test_failure_notifications_executor.py`** — edit 2 of 4. Replace:

````python
    """Run the real executor loop with the step itself faked. Returns what
    _run_build_stages returns: a BuildSnapshot snapshot, or None."""
````

with:

````python
    """Run the real executor loop with the step itself faked. Returns what
    _run_build_stages returns: a snapshot of the build as it ended, or None."""
````

**`backend/tests/test_failure_notifications_executor.py`** — edit 3 of 4. Replace:

````python
async def test_successful_build_returns_nothing(sf, monkeypatch):
    ids = await _seed(sf)

    assert await _run_stages(sf, monkeypatch, ids["build"], OK) is None
    assert await _build_status(sf, ids["build"]) == "success"


async def test_cancelled_build_returns_nothing(sf, monkeypatch):
````

with:

````python
async def test_successful_build_returns_a_snapshot_without_a_failure(sf, monkeypatch):
    ids = await _seed(sf)

    snapshot = await _run_stages(sf, monkeypatch, ids["build"], OK)

    assert await _build_status(sf, ids["build"]) == "success"
    assert snapshot.status == "success"
    assert (snapshot.failed_stage, snapshot.failed_step, snapshot.failed_step_id) == ("", "", None)


async def test_cancelled_build_returns_a_cancelled_snapshot(sf, monkeypatch):
````

**`backend/tests/test_failure_notifications_executor.py`** — edit 4 of 4. Replace:

````python
    assert await _run_stages(sf, monkeypatch, ids["build"], OK, on_step=cancel) is None
    assert await _build_status(sf, ids["build"]) == "cancelled"
````

with:

````python
    snapshot = await _run_stages(sf, monkeypatch, ids["build"], OK, on_step=cancel)

    assert await _build_status(sf, ids["build"]) == "cancelled"
    assert snapshot.status == "cancelled" and snapshot.failed_step == ""
````

- [ ] **Step 2: Run the tests and see them fail**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events_executor.py tests/test_failure_notifications_executor.py
```

Expected: `23 failed, 12 passed`. Most failures are `AttributeError: module 'app.services.build_executor' has no attribute '_wait_for_background_sends'`. The four `test_a_build_that_is_not_pending_is_not_run_and_sends_nothing` cases already pass: they pin behaviour that must not change.

- [ ] **Step 3: Add the hooks**

**`backend/app/services/build_executor.py`** — edit 1 of 9. Replace:

````python
from app.services.build_notifications import BuildSnapshot, send_build_notifications
````

with:

````python
from app.services.build_notifications import (
    BuildSnapshot,
    notifications_by_event,
    send_build_notifications,
)
````

**`backend/app/services/build_executor.py`** — edit 2 of 9. Replace:

````python
    failed_build: BuildSnapshot | None = None
    try:
        failed_build = await _run_build_stages(
````

with:

````python
    snapshot: BuildSnapshot | None = None
    try:
        snapshot = await _run_build_stages(
````

**`backend/app/services/build_executor.py`** — edit 3 of 9. Replace:

````python
    # Last, and only after the agent is free and the next build is on its
    # way: sending can be slow, and must not hold either of them up.
    if failed_build is not None:
        await _send_notifications(failed_build, session_factory)
````

with:

````python
    # Last, and only after the agent is free and the next build is on its
    # way: sending can be slow, and must not hold either of them up. The
    # sends started while the build ran go first, so "started" cannot arrive
    # after "failed".
    if snapshot is not None:
        await _wait_for_background_sends(build_id)
        await _send_notifications(snapshot, session_factory)
````

**`backend/app/services/build_executor.py`** — edit 4 of 9. Replace:

````python
    """Inner routine that actually executes all stages/steps for a build.

    Returns a snapshot of the failure when the build ended as failed, so the
    caller can send the pipeline's failure notifications once the agent has
    been released; otherwise None.
    """
````

with:

````python
    """Inner routine that actually executes all stages/steps for a build.

    Returns a snapshot of the build as it ended, so the caller can send the
    pipeline's notifications once the agent has been released; None when the
    build did not run. ``on_start`` and ``on_waiting`` notifications are
    started from here, in the background.
    """
````

**`backend/app/services/build_executor.py`** — edit 5 of 9. Replace:

````python
        secrets, env_vars, builtins = await _load_scope_context(db, build)

        build_failed = False
````

with:

````python
        secrets, env_vars, builtins = await _load_scope_context(db, build)

        if await _lists_event(session_factory, build.pipeline_id, "on_start"):
            _send_in_background(
                _capture_snapshot(build, secrets, env_vars, builtins, status="running"),
                session_factory,
            )

        build_failed = False
````

**`backend/app/services/build_executor.py`** — edit 6 of 9. Replace:

````python
                    "step_type": step.step_type,
                })

                step_result = await _execute_step(
````

with:

````python
                    "step_type": step.step_type,
                })

                # A person has to act now; a wait_webhook waits for a machine.
                if step.step_type == "wait_input" and await _lists_event(
                    session_factory, build.pipeline_id, "on_waiting"
                ):
                    _send_in_background(
                        _capture_snapshot(
                            build, secrets, env_vars, builtins,
                            status="running", waiting=(stage, step),
                        ),
                        session_factory,
                    )

                step_result = await _execute_step(
````

**`backend/app/services/build_executor.py`** — edit 7 of 9. Replace:

````python
        # Snapshot the failure now, as plain values. The notification is sent
        # later, and the calls below can roll this session back, which expires
        # every object loaded in it.
        failed_build = (
            _capture_snapshot(build, secrets, env_vars, builtins)
            if final_status == "failed"
            else None
        )
````

with:

````python
        # Snapshot the build now, as plain values. The notifications are sent
        # later, and the calls below can roll this session back, which expires
        # every object loaded in it.
        snapshot = _capture_snapshot(build, secrets, env_vars, builtins)
````

**`backend/app/services/build_executor.py`** — edit 8 of 9. Replace:

````python
            db, redis_client, build, final_status
        )
        return failed_build
````

with:

````python
            db, redis_client, build, final_status
        )
        return snapshot
````

**`backend/app/services/build_executor.py`** — edit 9 of 9. Replace:

````python
def _capture_snapshot(
    build: Build,
    secrets: dict[str, str],
    env_vars: dict[str, str],
    builtins: dict[str, dict[str, str]],
) -> BuildSnapshot | None:
    """Snapshot a failed build for its notifications. Never raises: a build's
    result must not depend on its notifications."""
    try:
        return BuildSnapshot.capture(build, secrets, env_vars, builtins)
    except Exception:
        logger.exception("Could not snapshot failed build for its notifications")
        return None


async def _send_notifications(
    failed: BuildSnapshot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Send the pipeline's ``notifications.on_failure`` messages.

    Runs after the build has been committed as failed and its agent released,
    on its own database session and Redis client, so nothing here can affect
    the build or the executor. Never raises.
    """
    redis_client: aioredis.Redis | None = None
    try:
        settings = get_settings()
        redis_client = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
        channel = f"build:{failed.build_id}:logs"

        async def report(text: str) -> None:
            # Shown under the failed step, where the author is already looking.
            # A session of its own: the sending session may just have been
            # rolled back.
            if failed.failed_step_id is None:
                logger.warning("build %s: %s", failed.build_id, text)
                return
            async with session_factory() as log_db:
                step = await log_db.get(Step, failed.failed_step_id)
                if step is None:
                    logger.warning("build %s: %s", failed.build_id, text)
                    return
                await _emit_system_log(step, log_db, redis_client, channel, f"⚠️ {text}")

        async with session_factory() as db:
            await send_build_notifications(db, failed, report=report)
    except Exception:
        logger.exception("Failure notifications for build %s could not be sent", failed.build_id)
    finally:
````

with:

````python
def _capture_snapshot(
    build: Build,
    secrets: dict[str, str],
    env_vars: dict[str, str],
    builtins: dict[str, dict[str, str]],
    **moment: Any,
) -> BuildSnapshot | None:
    """Snapshot a build for its notifications; *moment* is passed on to
    ``BuildSnapshot.capture``. Never raises: a build's result must not depend
    on its notifications."""
    try:
        return BuildSnapshot.capture(build, secrets, env_vars, builtins, **moment)
    except Exception:
        logger.exception("Could not snapshot build for its notifications")
        return None


async def _lists_event(
    session_factory: async_sessionmaker[AsyncSession],
    pipeline_id: uuid.UUID,
    event: str,
) -> bool:
    """Whether the pipeline's YAML, as stored now, lists *event* in its
    ``notifications`` block. Asked on a session of its own, and never raises:
    a build must not depend on its notifications."""
    try:
        async with session_factory() as db:
            yaml_content = await db.scalar(
                select(Pipeline.yaml_content).where(Pipeline.id == pipeline_id)
            )
        return event in notifications_by_event(yaml_content)
    except Exception:
        logger.exception("Could not read the notifications of pipeline %s", pipeline_id)
        return False


# Sends started while a build runs (on_start, on_waiting), by build. The event
# loop keeps only weak references to tasks, so they are held here until done.
_background_sends: dict[uuid.UUID, set[asyncio.Task]] = {}


def _send_in_background(
    snapshot: BuildSnapshot | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Start sending the notifications for *snapshot* without waiting: a slow
    channel must not delay the build."""
    if snapshot is None:
        return
    build_id = snapshot.build_id
    task = asyncio.ensure_future(_send_notifications(snapshot, session_factory))
    tasks = _background_sends.setdefault(build_id, set())
    tasks.add(task)

    def forget(done: asyncio.Task) -> None:
        tasks.discard(done)
        if not tasks and _background_sends.get(build_id) is tasks:
            del _background_sends[build_id]

    task.add_done_callback(forget)


async def _wait_for_background_sends(build_id: uuid.UUID) -> None:
    """Wait for the sends started while the build ran. Never raises."""
    tasks = list(_background_sends.get(build_id, ()))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _send_notifications(
    snapshot: BuildSnapshot,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Send what the pipeline's ``notifications`` block asks for at the moment
    in *snapshot*.

    Runs on its own database session and Redis client, so nothing here can
    affect the build or the executor. Never raises.
    """
    redis_client: aioredis.Redis | None = None
    try:
        channel = f"build:{snapshot.build_id}:logs"

        async def report(text: str) -> None:
            # Shown under the step the author is most likely looking at.
            # A session of its own: the sending session may just have been
            # rolled back.
            nonlocal redis_client
            if snapshot.log_step_id is None:
                logger.warning("build %s: %s", snapshot.build_id, text)
                return
            async with session_factory() as log_db:
                step = await log_db.get(Step, snapshot.log_step_id)
                if step is None:
                    logger.warning("build %s: %s", snapshot.build_id, text)
                    return
                if redis_client is None:
                    redis_client = aioredis.from_url(
                        get_settings().MEGOOCI_REDIS_URL, decode_responses=True
                    )
                await _emit_system_log(step, log_db, redis_client, channel, f"⚠️ {text}")

        async with session_factory() as db:
            await send_build_notifications(db, snapshot, report=report)
    except Exception:
        logger.exception("Notifications for build %s could not be sent", snapshot.build_id)
    finally:
````

The last replacement ends at the `finally:` line of the old `_notify_build_failure` (renamed in Task 1); the lines after it, which close the Redis client, stay.

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events_executor.py tests/test_failure_notifications_executor.py
```

Expected: `35 passed` (25 of them in the new file).

- [ ] **Step 5: Run the whole backend suite, with warnings as errors**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -W error::RuntimeWarning
```

Expected: `712 passed`. A `RuntimeWarning` here would mean a background send was started and never awaited.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/build_executor.py backend/tests/test_notification_events_executor.py backend/tests/test_failure_notifications_executor.py
git commit -m "feat(builds): notify when a build starts, waits for approval and ends

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Teach the AI assistant

**Files:**
- Modify: `backend/app/api/v1/ai_assistant.py` (`SYSTEM_PROMPT` only)
- Modify: `backend/app/services/assistant/reference.py` (rule 10 of `TOOL_RULES`)
- Modify: `backend/tests/test_failure_notifications_docs.py` (the wording of rule 12)
- Test: `backend/tests/test_notification_events_docs.py` (new)

**Interfaces:**
- Consumes: `NOTIFICATION_EVENTS`, `validate_pipeline`, `notifications_by_event`.
- The section heading still starts with `## Notifications`, which is what makes it the `notifications` reference topic in tool mode. The section keeps exactly one YAML example, whose `on_failure` entries are still `deploy-alerts` and `ops-email` (an existing test reads them).
- `SYSTEM_PROMPT` is a normal triple-quoted string in which a line ending in ` \` continues on the next line. Keep those backslashes exactly as printed.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_notification_events_docs.py`:

```python
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
    assert "the previous finished build of the same pipeline and branch had failed" in section
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
```

Rule 12 is reworded; update the existing test that quotes it:

**`backend/tests/test_failure_notifications_docs.py`**. Replace:

````python
    assert "12. When the user wants to be told about failed builds" in rules
    assert "`notifications` block with `on_failure`" in rules
````

with:

````python
    assert "12. When the user wants to be told about a build starting, finishing, failing" in rules
    assert "`notifications` block with the matching event" in rules
    assert "never a `notify` step" in rules
````

- [ ] **Step 2: Run the tests and see them fail**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events_docs.py tests/test_failure_notifications_docs.py tests/test_assistant_reference.py
```

Expected: `10 failed, 18 passed`: the nine new tests and the reworded rule-12 test.

- [ ] **Step 3: Update the prompt and the tool-mode rule**

**`backend/app/api/v1/ai_assistant.py`** — edit 1 of 6. Replace:

````python
    allowed_users:
      - admin
      - lead
```

### notify — Send a notification via a configured channel
````

with:

````python
    allowed_users:
      - admin
      - lead
```

To tell approvers that a build is waiting for them, list a channel under \
`on_waiting` in the top-level `notifications` block.

### notify — Send a notification via a configured channel
````

**`backend/app/api/v1/ai_assistant.py`** — edit 2 of 6. Replace:

````python
A `notify` step runs only if the build reaches it. A build stops at the first \
failed step, so a `notify` step never reports a failure — use the top-level \
`notifications` block for that.
````

with:

````python
A `notify` step runs only if the build reaches it. A build stops at the first \
failed step, so a `notify` step never reports a failure — use the top-level \
`notifications` block for that, and for messages about a build starting or ending.
````

**`backend/app/api/v1/ai_assistant.py`** — edit 3 of 6. Replace:

````python
## Notifications — Tell people when a build fails
Add a top-level `notifications` block (not inside a stage) to send a message \
through a configured channel whenever a build of this pipeline fails. The \
server sends it, so it also goes out when the build's agent goes offline or \
stops responding. A build that is still waiting for an agent stays pending and sends nothing.

```yaml
version: 1
name: deploy-staging
notifications:
  on_failure:
    - deploy-alerts                     # short form: a channel name
    - channel: ops-email                # full form
````

with:

````python
## Notifications — Tell people about builds
Add a top-level `notifications` block (not inside a stage) to send a message \
through a configured channel when something happens to a build of this \
pipeline. The server sends it, so it also goes out when the build's agent goes \
offline or stops responding.

```yaml
version: 1
name: deploy-staging
notifications:
  on_start:
    - team-chat
  on_failure:
    - deploy-alerts                     # short form: a channel name
    - channel: ops-email                # full form
````

**`backend/app/api/v1/ai_assistant.py`** — edit 4 of 6. Replace:

````python
        at ${{ build.failed_stage }} / ${{ build.failed_step }}
        ${{ build.url }}
stages:
  - name: deploy
    steps:
      - run: "./deploy.sh"
```

`on_failure` is the only event, and it must be a non-empty list. Each entry is \
either a channel name or a mapping whose only fields are `channel` (required), \
`message`, `subject` and `recipient`. Channels are configured by admins in the \
Notification Channels UI (email, Slack or Telegram) — never invent a channel \
name; ask the user which channel to use if they have not said.

Without `message`, a default is sent: the pipeline name, build number, branch, \
commit, the stage and step that failed, and a link to the build. A custom \
`message` or `subject` can use every placeholder, plus three that exist only \
here: `${{ build.failed_stage }}`, `${{ build.failed_step }}` and \
`${{ build.url }}`. For an email channel set `recipient`; without it the \
message goes to the channel's own sender address. A successful or cancelled \
build sends nothing.
````

with:

````python
        at ${{ build.failed_stage }} / ${{ build.failed_step }}
        ${{ build.url }}
  on_complete:
    - team-chat
stages:
  - name: deploy
    steps:
      - run: "./deploy.sh"
```

Events — each is optional, and the block needs at least one:
- `on_start` — the build starts running.
- `on_waiting` — the build pauses at a `wait_input` step and needs approval.
- `on_success` — the build ends successfully.
- `on_fixed` — the build ends successfully and the previous finished build of \
the same pipeline and branch had failed.
- `on_failure` — the build ends as failed.
- `on_cancelled` — a running build is cancelled.
- `on_complete` — the build ends, whatever the result.

Each event is a non-empty list. Each entry is either a channel name or a \
mapping whose only fields are `channel` (required), `message`, `subject` and \
`recipient`. Channels are configured by admins in the Notification Channels UI \
(email, Slack or Telegram) — never invent a channel name; ask the user which \
channel to use if they have not said.

When a build ends, a channel listed under several matching events gets one \
message, from the most specific event: `on_fixed` before `on_success` before \
`on_complete`, and `on_failure` or `on_cancelled` before `on_complete`. So with \
`team-chat` under `on_complete` and also under `on_failure` with its own \
message, a failed build sends `team-chat` only the `on_failure` message. Do not \
repeat a channel under `on_success` when it is already under `on_complete`, \
unless the messages should differ.

Without `message`, a default is sent: what happened, the pipeline name, build \
number, branch, commit, the step that failed or is waiting, and a link to the \
build. A custom `message` or `subject` can use every placeholder. \
`${{ build.status }}` is `running`, `success`, `failed` or `cancelled`, which \
lets one `on_complete` message fit every result. These exist only here: \
`${{ build.url }}`; `${{ build.failed_stage }}` and `${{ build.failed_step }}` \
for a failed build; `${{ build.waiting_stage }}` and `${{ build.waiting_step }}` \
for `on_waiting`. For an email channel set `recipient`; without it the message \
goes to the channel's own sender address.

A build that never started sends nothing: one still waiting for an agent \
stays pending and sends nothing, and one cancelled before it started sends no \
`on_cancelled` and no `on_complete`.
````

**`backend/app/api/v1/ai_assistant.py`** — edit 5 of 6. Replace:

````python
notifications:          # optional — who is told when a build fails
  on_failure:
````

with:

````python
notifications:          # optional — who is told about builds (on_start, on_complete, ...)
  on_failure:
````

**`backend/app/api/v1/ai_assistant.py`** — edit 6 of 6. Replace:

````python
12. When the user wants to be told about failed builds, add the top-level \
`notifications` block with `on_failure` — never a `notify` step at the end of \
the pipeline, which does not run after a failure.
````

with:

````python
12. When the user wants to be told about a build starting, finishing, failing, \
being cancelled, recovering or waiting for approval, add the top-level \
`notifications` block with the matching event (`on_start`, `on_complete`, \
`on_failure`, `on_cancelled`, `on_fixed`, `on_waiting`) — never a `notify` step \
at the end of the pipeline, which does not run after a failure or a cancellation.
````

**`backend/app/services/assistant/reference.py`**. Replace:

````python
10. To be told about failed builds, use the top-level `notifications` block — \
never a `notify` step at the end, which does not run after a failure.
````

with:

````python
10. To be told about a build starting, finishing, failing, being cancelled, \
recovering or waiting for approval, use the top-level `notifications` block — \
never a `notify` step at the end, which does not run after a failure. Read the \
`notifications` topic for its events before you write it.
````

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events_docs.py tests/test_failure_notifications_docs.py tests/test_assistant_reference.py tests/test_http_request_docs.py
```

Expected: `35 passed`.

- [ ] **Step 5: Run the whole backend suite**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `721 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/ai_assistant.py backend/app/services/assistant/reference.py backend/tests/test_notification_events_docs.py backend/tests/test_failure_notifications_docs.py
git commit -m "feat(ai): teach the pipeline assistant every notification event

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Pipeline docs panel and README

**Files:**
- Modify: `frontend/src/components/pipeline/docs-panel.tsx` (the `notifications`, `wait_input` and `notify` sections)
- Modify: `README.md`
- Test: `backend/tests/test_notification_events_docs_panel.py` (new)

**Interfaces:**
- Consumes: `validate_pipeline`, `notifications_by_event`, `NOTIFICATION_EVENTS`.
- The frontend has no test harness, so the test reads the section's title, description and YAML example out of `docs-panel.tsx` with a regular expression and runs the example through the validator. It skips when the frontend sources are not in the checkout.
- In `docs-panel.tsx` each section is an object with `id`, `title`, `icon`, `description` (one double-quoted string on one line) and `yaml` (a template literal in which `${{` is written `\${{`). Keep that shape: the test depends on it.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_notification_events_docs_panel.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events_docs_panel.py
```

Expected: `3 failed`. The first: `assert 'Failure Notifications' == 'Build Notifications'`.

- [ ] **Step 3: Update the docs panel and the README**

**`frontend/src/components/pipeline/docs-panel.tsx`** — edit 1 of 3. Replace:

````tsx
    title: "Failure Notifications",
    icon: <BellRing className="h-4 w-4" />,
    description:
      "Be told when a build of this pipeline fails. Add a top-level notifications block (beside runs_on, not inside a stage) listing the channels to notify; channels are set up by admins under Integrations > Notification Channels. The server sends the message, so it also goes out when the build's agent goes offline or stops responding; a build still waiting for an agent stays pending and sends nothing. Each entry is a channel name, or a mapping with channel plus an optional recipient, subject (email) and message. Without a message, a default is sent with the pipeline, build number, branch, commit, the stage and step that failed, and a link to the build. For an email channel set recipient, otherwise the message goes to the channel's own sender address. Successful and cancelled builds send nothing. A channel that does not exist or is disabled is reported in the build log.",
    yaml: `version: 1
name: deploy-staging
notifications:
  on_failure:
    - deploy-alerts                     # short form: a channel name
    - channel: ops-email                # full form
      recipient: oncall@example.com     # optional override
      subject: "Staging deploy failed"  # optional (email only)
      message: |                        # optional; a default is sent if omitted
        Build #\${{ build.number }} of \${{ pipeline.name }} failed
        at \${{ build.failed_stage }} / \${{ build.failed_step }}
        \${{ build.url }}
stages:
````

with:

````tsx
    title: "Build Notifications",
    icon: <BellRing className="h-4 w-4" />,
    description:
      "Be told what happens to builds of this pipeline. Add a top-level notifications block (beside runs_on, not inside a stage) and list channels under the events you care about; channels are set up by admins under Integrations > Notification Channels. The events: on_start (the build starts running), on_waiting (it pauses at a wait_input step and needs approval), on_success (it ends successfully), on_fixed (it succeeds and the previous finished build of the same pipeline and branch had failed), on_failure (it fails), on_cancelled (a running build is cancelled) and on_complete (it ends, whatever the result). When a build ends, a channel listed under several matching events gets one message, from the most specific event: on_fixed before on_success before on_complete, and on_failure or on_cancelled before on_complete. Each entry is a channel name, or a mapping with channel plus an optional recipient, subject (email) and message. Without a message, a default is sent with what happened, the pipeline, build number, branch, commit, the step that failed or is waiting, and a link to the build; in a message of your own, build.status is running, success, failed or cancelled. For an email channel set recipient, otherwise the message goes to the channel's own sender address. The server sends the messages, so they also go out when the build's agent goes offline or stops responding. A build that never started sends nothing: one still waiting for an agent stays pending, and one cancelled before it started is not announced. A channel that does not exist or is disabled is reported in the build log.",
    yaml: `version: 1
name: deploy-staging
notifications:
  on_start:
    - team-chat                         # short form: a channel name
  on_failure:
    - channel: team-chat                # full form, with a message of its own
      message: |
        Build #\${{ build.number }} of \${{ pipeline.name }} failed
        at \${{ build.failed_stage }} / \${{ build.failed_step }}
        \${{ build.url }}
    - channel: ops-email
      recipient: oncall@example.com     # optional override
      subject: "Staging deploy failed"  # optional (email only)
  on_complete:
    - team-chat                         # every result; a failure uses the entry above
stages:
````

**`frontend/src/components/pipeline/docs-panel.tsx`** — edit 2 of 3. Replace:

````tsx
      "Pause the pipeline until a user manually approves or rejects. Great for production deployment gates.",
````

with:

````tsx
      "Pause the pipeline until a user manually approves or rejects. Great for production deployment gates. To tell approvers that a build is waiting for them, list a channel under on_waiting in the top-level notifications block (see Build Notifications).",
````

**`frontend/src/components/pipeline/docs-panel.tsx`** — edit 3 of 3. Replace:

````tsx
so to be told about failed builds use the top-level notifications block (see Failure Notifications).",
````

with:

````tsx
so to be told about failed builds, or about builds starting and ending, use the top-level notifications block (see Build Notifications).",
````

**`README.md`**. Replace:

````markdown
To be told when a build fails, add a top-level `notifications` block. The server sends the message through a channel configured under Notification Channels, so it also goes out when the build's agent goes offline mid-build (a build still waiting for an agent stays pending and sends nothing):

```yaml
name: deploy-staging
notifications:
  on_failure:
    - deploy-alerts                   # a channel name
    - channel: ops-email              # or a mapping
      recipient: oncall@example.com
stages:
  - name: deploy
    steps:
      - run: ./deploy.sh
```

Without a `message`, a default is sent with the pipeline, build number, branch, commit, the stage and step that failed, and a link to the build. A `notify` step at the end of a pipeline cannot do this: a build stops at the first failed step.
````

with:

````markdown
To be told what happens to a pipeline's builds, add a top-level `notifications` block. The server sends the messages through channels configured under Notification Channels, so they also go out when the build's agent goes offline mid-build:

```yaml
name: deploy-staging
notifications:
  on_start:
    - team-chat                       # a channel name
  on_failure:
    - channel: ops-email              # or a mapping
      recipient: oncall@example.com
  on_complete:
    - team-chat
stages:
  - name: deploy
    steps:
      - run: ./deploy.sh
```

| Event | Sent when |
|---|---|
| `on_start` | the build starts running |
| `on_waiting` | the build pauses at a `wait_input` step and needs approval |
| `on_success` | the build ends successfully |
| `on_fixed` | the build succeeds and the previous finished build of the same pipeline and branch had failed |
| `on_failure` | the build fails |
| `on_cancelled` | a running build is cancelled |
| `on_complete` | the build ends, whatever the result |

When a build ends, a channel listed under several matching events gets one message, from the most specific event (`on_fixed`, then `on_success`, then `on_complete`; `on_failure` or `on_cancelled`, then `on_complete`). A build that never started sends nothing: one still waiting for an agent stays pending, and one cancelled before it started is not announced.

Without a `message`, a default is sent with what happened, the pipeline, build number, branch, commit, the step that failed or is waiting, and a link to the build. In a message of your own, `${{ build.status }}` is `running`, `success`, `failed` or `cancelled`. A `notify` step at the end of a pipeline cannot do this: a build stops at the first failed step.
````

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_notification_events_docs_panel.py
```

Expected: `3 passed`.

- [ ] **Step 5: Type-check the frontend**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/pipeline/docs-panel.tsx README.md backend/tests/test_notification_events_docs_panel.py
git commit -m "docs(pipeline): document every build notification event

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Final verification

- [ ] **Step 1: Backend suite**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -W error::RuntimeWarning
```

Expected: `724 passed`.

- [ ] **Step 2: Frontend type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 3: Nothing outside the plan changed**

Run from the repository root:

```bash
git diff main --stat -- agent backend/alembic backend/app/models backend/app/config.py
```

Expected: no output — no agent change, no migration, no model change, no setting.

- [ ] **Step 4: Manual checks (need the running app, a build agent and a real notification channel)**

Report each as passed, failed or not run. Do not report them as passed without doing them.

1. A pipeline with `on_start` and `on_complete` on one channel: a successful build posts "started", then "succeeded".
2. The same pipeline with a custom `on_failure` on that channel: a failing build posts "started" and the custom failure message, and nothing else.
3. `on_fixed`: a failing build followed by a successful one on the same branch posts "is fixed"; the next successful build does not.
4. `on_waiting` with a `wait_input` step: the message arrives when the build reaches the step, naming it; approving lets the build continue.
5. Cancel a running build: `on_cancelled` arrives. Cancel a build that is still pending: nothing arrives.
6. An unknown channel under `on_start`: the build log shows one warning line under the first step and the build is unaffected.
7. The docs panel shows "Build Notifications" with the seven events; inserting its example into the editor validates.
8. Ask the AI assistant to "tell team-chat when a deploy starts and when it ends": it adds `on_start` and `on_complete` to the `notifications` block, not a `notify` step.
