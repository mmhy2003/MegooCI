# More Build Notification Events — Design

**Date:** 2026-10-08
**Status:** Approved (design)
**Area:** pipeline YAML, build executor, notification sending, pipeline docs panel, AI assistant
**Builds on:** `docs/superpowers/specs/2026-10-08-failure-notifications-design.md`

## Problem

A pipeline can name channels to notify when a build fails (`notifications.on_failure`). It
cannot say "tell us when a deploy starts", "tell us when it is done", "tell approvers a
build is waiting for them", or "tell us when the pipeline is green again". Teams work around
this with `notify` steps, which do not run after a failure or a cancellation and cannot know
how the build ended.

## Goals

- Six more events in the `notifications` block: `on_start`, `on_waiting`, `on_success`,
  `on_fixed`, `on_cancelled`, `on_complete`.
- Each event takes the same entries `on_failure` takes today.
- A channel listed under several events that match the same build gets one message, from
  the most specific event.
- Existing pipelines, and existing `on_failure` blocks, behave exactly as before.
- The pipeline docs panel, the README and the AI assistant know the new events.

## Non-goals

- Events that need a timer: a build queued too long, a build running too long.
- Per-stage or per-step events. A `notify` step covers those.
- A "status changed" event. `on_failure` and `on_fixed` cover the cases people act on.
- New placeholders beyond the two for `on_waiting` (for example a build duration).
- Filtering an event by branch or trigger.
- Any change to the agent, the database schema, the compiled build graph, or how channels
  are configured.

## YAML

```yaml
version: 1
name: deploy-production
notifications:
  on_start:
    - team-chat
  on_waiting:
    - channel: approvers-mail
      recipient: leads@example.com
  on_fixed:
    - team-chat
  on_failure:
    - channel: team-chat
      message: "🔥 ${{ pipeline.name }} failed at ${{ build.failed_step }}"
  on_complete:
    - team-chat
stages:
  - name: deploy
    steps:
      - run: ./deploy.sh
```

### Rules

- `notifications` is a mapping. Its allowed keys are `on_start`, `on_waiting`, `on_success`,
  `on_fixed`, `on_failure`, `on_cancelled` and `on_complete`. Each is optional, but the
  block must contain at least one. (Today it must contain `on_failure`.)
- Each event is a non-empty list. Each entry is a non-empty channel name, or a mapping with
  `channel` (required) and optional `message`, `subject`, `recipient`, each a non-empty
  string. These are today's `on_failure` rules, applied to every event.
- The block is read from the pipeline's YAML as stored when the event happens, not as it
  was when the build was triggered.

## Events

| Event | Fires when |
|---|---|
| `on_start` | The build starts running: its status changes from `pending` to `running`. |
| `on_waiting` | The build reaches a `wait_input` step and starts waiting for approval. Once per such step. |
| `on_success` | The build ends with status `success`. |
| `on_fixed` | The build ends with status `success`, and the previous build of the same pipeline is a failure (see below). |
| `on_failure` | The build ends with status `failed`. Unchanged. |
| `on_cancelled` | A running build ends with status `cancelled`. |
| `on_complete` | The build ends with status `success`, `failed` or `cancelled`. |

### A build that never ran sends nothing

End-of-build events are sent by the build executor, so they exist only for a build that
started running. A build cancelled while still `pending` — by a user, or because a newer
trigger replaced it — sends no `on_cancelled` and no `on_complete`, as it sent no
`on_start`. A build that is rejected before it runs (invalid YAML) sends nothing either, as
today.

### `on_waiting` is for approvals

It fires for `wait_input`, where a person must act. It does not fire for `wait_webhook`,
which waits for another system. The in-app notification approvers already receive is
unchanged.

### What "fixed" means

A successful build is a fix when the most recent earlier build that

- belongs to the same pipeline,
- has the same branch (two builds without a branch count as the same branch), and
- ended as `success` or `failed`

ended as `failed`. Cancelled builds, and builds still pending or running, are skipped: fail,
cancel, success is a fix. The first build of a pipeline or of a branch is never a fix.

The lookup is one query, made only when the pipeline lists `on_fixed`.

## Overlap: one message per channel

When a build ends, several events match it. The events are tried from most to least
specific, and each destination gets the entry from the first event that lists it:

| Build ended as | Order |
|---|---|
| `success`, and it is a fix | `on_fixed`, `on_success`, `on_complete` |
| `success` | `on_success`, `on_complete` |
| `failed` | `on_failure`, `on_complete` |
| `cancelled` | `on_cancelled`, `on_complete` |

- A **destination** is a channel name plus the entry's `recipient` (none counts as its own
  value). Two email entries on the same channel with different recipients are two
  destinations and both are sent.
- Within one event nothing is removed: listing the same destination twice under one event
  sends twice, as `on_failure` does today.
- `on_start` and `on_waiting` happen at other moments and never overlap with anything.

In the example above, a failed build sends only the custom `on_failure` message to
`team-chat`; a build that recovers sends only the `on_fixed` message; any other build sends
the `on_complete` message.

## Messages

### Placeholders

A custom `message` or `subject` can use every placeholder a step can use, plus the ones
that exist only in notification messages:

| Placeholder | Value |
|---|---|
| `${{ build.status }}` | `running` for `on_start` and `on_waiting`; otherwise `success`, `failed` or `cancelled`. |
| `${{ build.url }}` | Link to the build. Unchanged. |
| `${{ build.failed_stage }}`, `${{ build.failed_step }}` | The first failed step, for a failed build. Empty for every other outcome. Unchanged. |
| `${{ build.waiting_stage }}`, `${{ build.waiting_step }}` | **New.** The `wait_input` step the build is waiting at. Empty outside `on_waiting`. |

`${{ build.status }}` is what makes one custom `on_complete` message work for every
outcome.

### Default subject and message

Used when an entry does not write its own. The subject line:

| Event | Subject |
|---|---|
| `on_start` | `Build #12 of deploy-production started` |
| `on_waiting` | `Build #12 of deploy-production is waiting for approval` |
| `on_success` | `Build #12 of deploy-production succeeded` |
| `on_fixed` | `Build #12 of deploy-production is fixed` |
| `on_failure` | `Build #12 of deploy-production failed` (unchanged) |
| `on_cancelled` | `Build #12 of deploy-production was cancelled` |
| `on_complete` | The subject of the outcome: succeeded, failed, or was cancelled. |

The default message is the subject, then the project, branch and short commit on one line
when known, then one event-specific line — `Failed at: stage "…", step "…"` for a failure,
`Waiting at: stage "…", step "…"` for `on_waiting` — and the build link. This is today's
failure message with the first and third lines varying.

Escaping is unchanged: for Telegram and Slack channels the default message and the values
substituted for built-in placeholders are escaped; text the author wrote is not.

## Sending (server)

Everything runs on the server through the existing channel code. Three moments:

1. **Start.** Right after the build is marked `running`, the executor starts sending
   `on_start` in the background and continues with the build. A slow channel never delays
   the first step.
2. **Waiting.** When the executor reaches a `wait_input` step, it starts sending
   `on_waiting` in the background and the step begins waiting as before.
3. **End.** Where `on_failure` is sent today: after the final status is saved, the agent is
   released and the next pending build is dispatched. Before sending, the executor waits
   for background sends of this build that are still running, so "started" cannot arrive
   after "failed".

The messages of one build go out in the order things happened. A background send waits for
the build's earlier ones, so when the first step is an approval, "started" still arrives
before "waiting for approval". The cost is that a stalled `on_start` channel can delay the
approval message by up to its 30-second limit per stalled entry; the approval step itself
never waits.

The executor also waits for a build's background sends when it stops because of an error,
so none is left unfinished holding a database session.

The rules of failure notifications apply to every event:

- Best-effort. Nothing here can change a build's status, fail a step, or raise into the
  executor.
- Entries are independent. A problem with one never stops the others.
- Each send has a 30-second limit.
- Sending uses its own database session and plain values captured from the build, never
  objects tied to the executor's session.
- A send is recorded as a delivery, as today.

### When something goes wrong

A problem with an entry (unknown or disabled channel, a delivery that failed) is written to
the build log as one short line, without the provider's error text, which can quote a
webhook URL or a token. The line goes where the author will look:

| Event | Log line appears under |
|---|---|
| `on_failure` | The failed step (unchanged). |
| `on_waiting` | The waiting step. |
| `on_start` | The build's first step. |
| `on_success`, `on_fixed`, `on_cancelled`, `on_complete` | The build's last step that ran; for a failed build, the failed step. |

If there is no such step, the line goes to the server log.

## Validation (server)

`validate_pipeline` and `validate_pipeline_definition` accept the seven events and report,
with line numbers as today:

- an unknown key under `notifications` (the message lists the allowed events);
- an event that is not a non-empty list;
- an entry that breaks the entry rules. Messages start with
  `notifications.<event>[<index>]: `.

The compiler ignores the block, as today.

## Documentation

### Pipeline docs panel (`frontend/src/components/pipeline/docs-panel.tsx`)

- The "Failure Notifications" section becomes **"Build Notifications"**. Its description
  lists the seven events and when each fires, states the one-message-per-channel rule, and
  says that a build that never started sends nothing.
- Its YAML example shows `on_start`, `on_failure` with a custom message, and `on_complete`
  on the same channel, so the overlap rule is visible in the example.
- The `wait_input` section mentions `on_waiting`.
- The `notify` step section keeps its note that a `notify` step does not run after a
  failure, and points to the block for start, end and failure messages.

### AI assistant (`backend/app/api/v1/ai_assistant.py`, `backend/app/services/assistant/reference.py`)

- The `## Notifications` section of `SYSTEM_PROMPT` is rewritten for all seven events: the
  table of events, the overlap rule, the `on_fixed` definition in one sentence, the
  placeholders including the two new ones, and an example. Its heading changes from
  "Tell people when a build fails" to cover build events in general; it still starts with
  `## Notifications`, which is what makes it the `notifications` reference topic.
- The structure example in `## Pipeline Structure` shows the block with a comment naming
  the events.
- Rule 12 of `SYSTEM_PROMPT` and rule 10 of the tool-mode rules are widened: to be told
  about a build starting, finishing, failing, being cancelled, recovering or waiting for
  approval, use the `notifications` block — never a `notify` step at the end of the
  pipeline.
- The `wait_input` step section mentions `on_waiting`.

### README

The failure-notifications example and its text are extended to the new events.

## Testing

**Validator (pytest)**
- Each event accepted alone and together; `on_failure` blocks from before still valid.
- An unknown event, an empty block, an event that is not a list, an empty list, and each
  bad entry shape, for a new event, with the right line number and message prefix.

**Selection (pytest, no database)**
- For each outcome, which entries are chosen: every row of the overlap table.
- The same channel under two matching events sends once, from the more specific one.
- The same channel with different recipients sends to both.
- The same destination twice under one event sends twice.
- A build that is not a fix never uses `on_fixed`.

**Fixed (pytest, in-memory database)**
- Failed then success is a fix; success then success is not.
- A failure on another branch does not count; two builds without a branch do.
- A cancelled or still-running build in between is skipped.
- The first build of a pipeline is not a fix.
- No query is made when the pipeline does not list `on_fixed`.

**Messages (pytest)**
- Default subject and message for every event; `on_complete` follows the outcome.
- `${{ build.status }}` for each event; the waiting placeholders; failed placeholders empty
  outside a failure.
- Escaping for a Telegram channel on a new event.

**Executor (pytest, in-memory database, channels stubbed)**
- `on_start` is sent once when the build starts, and the first step does not wait for it.
- `on_waiting` is sent once per `wait_input` step, and not for `wait_webhook`.
- A successful, a failed and a cancelled build each send the right end events.
- A pending build that is cancelled sends nothing.
- The end messages are sent after a still-running `on_start` send.
- A send that raises, or hangs past its limit, never changes the build's result.
- Problems are logged under the step the table above names.

**Documentation (pytest)**
- Every YAML example in the prompt's notifications section passes the validator.
- The prompt names all seven events; the `notifications` reference topic contains them.

**Frontend**
- `npx tsc --noEmit`. The frontend has no test harness, so a backend test reads the
  notifications example out of `docs-panel.tsx` and runs it through the validator, and
  checks that the section names all seven events.

## Files touched

- `backend/app/services/pipeline_compiler.py` — the event list; validation messages.
- `backend/app/services/build_notifications.py` — events, selection, the fix lookup,
  per-event default messages, the snapshot generalised from a failed build to any event.
- `backend/app/services/build_executor.py` — the start and waiting hooks; the end hook for
  every outcome.
- `backend/app/api/v1/ai_assistant.py`, `backend/app/services/assistant/reference.py` — the
  prompt and the tool-mode rule.
- `frontend/src/components/pipeline/docs-panel.tsx` — the docs sections.
- `README.md` — the example.
- `backend/tests/` — new and extended tests.

## Rollback

No schema change and no setting. Reverting the code makes the six new keys validation
errors again; pipelines that use them must drop them first, or they cannot be saved.
