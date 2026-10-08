# Build Failure Notifications — Design

**Date:** 2026-10-08
**Status:** Approved (design)
**Area:** Pipeline YAML, build executor, pipeline docs, AI assistant prompt

## Problem

When a build fails, the only notice is an in-app notification to the user who triggered it.
A build started by a Git push has no such user, so nobody is told.

A pipeline author cannot fix this in YAML today:

- A build stops at the first failed step. Later steps and stages do not run, so a `notify`
  step placed at the end never fires on failure.
- `when` conditions are parsed but not enforced by the executor, so there is no way to
  express "run this only on failure".

This design lets a pipeline declare who is told when one of its builds fails.

## Goals

- A pipeline author states, in the pipeline YAML, which notification channels receive a
  message when a build of that pipeline fails.
- The message is sent by the server, so it also goes out when the failure is that no agent
  was available or an agent stopped responding.
- A useful default message, with the option to write a custom one.
- Reuse the existing notification channels and delivery history.
- The in-app pipeline docs and the AI assistant know the new YAML.

## Non-goals

- Notifications on success, on cancellation, or on the first success after a failure. The
  block is shaped so `on_success` and `on_fixed` can be added later.
- Filters such as "only on branch main".
- Running arbitrary steps on failure, and fixing the unenforced `when` conditions. Both are
  separate work.
- Targets other than the existing channel types (email, Slack, Telegram).
- A form in the UI for configuring this.
- Checking at save time that a named channel exists.
- Notifying for a build that fails pipeline validation at trigger time. That failure means
  the stored YAML is broken, so the block cannot be read.
- Any change to the build agent or the database schema.

## Approach

Three approaches were considered:

- **A. A `notifications` block in the pipeline YAML, sent by the server (chosen).** Lives
  with the rest of the pipeline definition, needs no agent, and reuses the channel code.
- **B. A setting on the pipeline page.** No YAML needed, but it requires a migration, new
  endpoints, a new UI section and a non-admin way to list channels, and the setting does not
  travel with the pipeline definition.
- **C. Steps that run on failure.** Most flexible, but the executor would have to keep going
  after a failure, and it cannot report failures where no agent could run.

## YAML

```yaml
name: deploy-staging-inbox
notifications:
  on_failure:
    - deploy-alerts
    - channel: ops-email
      recipient: oncall@example.com
      subject: "Staging deploy failed"
      message: |
        Build #${{ build.number }} of ${{ pipeline.name }} failed
        at ${{ build.failed_stage }} / ${{ build.failed_step }}
        ${{ build.url }}
stages:
  - name: deploy
    steps:
      - run: ./deploy.sh
```

### Rules

- `notifications` is a top-level key, beside `runs_on`. It must be a mapping.
- Its only allowed key is `on_failure`. Any other key is a validation error.
- `on_failure` must be a non-empty list. Each entry is one of:
  - a non-empty string: the name of a notification channel;
  - a mapping with these keys and no others:

    | Key | Required | Meaning |
    |---|---|---|
    | `channel` | yes | Channel name, as configured under Notification Channels. Non-empty string. |
    | `message` | no | Message text. Non-empty string. Placeholders allowed. |
    | `subject` | no | Subject line, used by email channels. Non-empty string. Placeholders allowed. |
    | `recipient` | no | Overrides the channel's default recipient, as in the `notify` step. Non-empty string. |

- The same channel may appear more than once (for example with different recipients).
- A pipeline without the block behaves exactly as today.

### Placeholders

`message` and `subject` go through the same interpolation as step configs, so
`${{ secrets.X }}`, `${{ env.X }}`, `${{ build.X }}`, `${{ pipeline.X }}`,
`${{ project.X }}` and `${{ megooci.X }}` all work. Three build values exist only here,
because they are known only when the build has ended:

| Placeholder | Value |
|---|---|
| `${{ build.failed_stage }}` | Name of the stage that failed, or empty. |
| `${{ build.failed_step }}` | Name of the step that failed, or empty. |
| `${{ build.url }}` | `{MEGOOCI_PUBLIC_URL}/builds/{build id}`. |

`${{ build.status }}` is `failed`.

### Default message and subject

When `message` is omitted:

```
Build #428 of deploy-staging-inbox failed
Project: Inbox Staging | Branch: develop | Commit: 3f2a9c1
Failed at: stage "deploy", step "apply manifests"
https://ci.example.com/builds/5d0c…
```

- The commit is shortened to 7 characters. The `Commit` part is left out when the build has
  no commit, and the `Branch` part when it has no branch.
- The `Failed at` line is left out when no failed step is recorded.

When `subject` is omitted it is `Build #428 of deploy-staging-inbox failed`.

## Validation (server)

`backend/app/services/pipeline_compiler.py`:

- A new helper `_validate_notifications(value)` enforces the rules above and is called from
  `_structure_errors` when the top-level mapping has a `notifications` key.
- Errors use the existing message style and carry a line: the line of the offending entry
  when it is a mapping, otherwise the line of the `notifications` mapping.
- The compiler ignores the block. It does not affect stages, steps or `runs_on`.

## Sending (server)

A new module `backend/app/services/build_notifications.py` owns everything about this
feature except the YAML rules:

- `failure_notifications(yaml_content)` reads the block from a YAML string and returns the
  entries in a normalized form (channel, message, subject, recipient). It never raises: for
  missing, unparseable or malformed input it returns an empty list. The validator is what
  reports mistakes; this reader only has to be safe.
- `send_failure_notifications(...)` builds the placeholder values, renders each entry's
  message and subject (or the defaults), and sends each one through the existing
  `send_notification`, which records a `NotificationDelivery` row linked to the build.

`backend/app/services/build_executor.py` calls it once, at the point where a build's final
status has been committed, when that status is `failed`. It sits beside the existing in-app
notification, which stays as it is.

- The block is read from the pipeline's YAML as stored when the build ends, not as it was
  when the build was triggered. No snapshot column is needed; an edit made while a build is
  running applies to that build.
- Entries are independent. A problem with one never stops the others.
- Sending never changes the build's status or raises into the executor.
- It covers every way a build can fail during execution: a failing step, no agent available,
  and an agent that stops responding.

### When something goes wrong

For each entry that cannot be delivered, one system log line is added to the build log under
the failed step, so the author sees it where they are already looking:

| Situation | Log line says |
|---|---|
| No channel with that name | the channel was not found |
| The channel is disabled | the channel is disabled |
| The send failed | the delivery failed, and an administrator can find the error in the delivery history |

The provider's error text is never put in the build log. It can quote the channel's webhook
URL or bot token, and the build log is readable by everyone who can see the build. The error
stays on the delivery row, as today.

When there is no failed step to attach the line to, the problem is written to the server log
instead.

### Channel types

The block works with all three existing channel types, because it sends through the same
code as the `notify` step:

- **Email:** `subject` is used. Without `recipient`, the existing sender delivers to the
  channel's own sender address, so email entries should normally set `recipient`. The docs
  say so.
- **Slack:** `recipient`, if set, is sent as a channel override.
- **Telegram:** the message goes to the channel's default chat, or to `recipient`. The
  existing sender tells Telegram to read the message as HTML, and Telegram rejects a stray
  `<` or `&`. So for a Telegram channel the default message is HTML-escaped, and in a custom
  message the values substituted for `build`, `pipeline`, `project` and `megooci`
  placeholders are HTML-escaped. Text the author wrote is left alone, so Telegram's own tags
  such as `<b>` still work. Values from `secrets` and `env` placeholders are not escaped.

## Documentation

- `backend/app/api/v1/ai_assistant.py` (`SYSTEM_PROMPT`):
  - a new `## Notifications — Tell people when a build fails` section after the `runs_on`
    section, with the YAML, both entry forms, the new placeholders and the default message;
  - the `## Pipeline Structure` example shows the optional top-level `notifications` key;
  - the `notify` step section states that a `notify` step does not run after a failure,
    and its example is corrected: it used `${{ build.commit_sha }}`, which the executor does
    not provide (the value is `${{ build.commit }}`), so it rendered as empty;
  - a new rule: when the user wants to be told about failed builds, use the top-level
    `notifications` block, not a `notify` step at the end.
- `frontend/src/components/pipeline/docs-panel.tsx`:
  - a new "Failure Notifications" section after "Target Agent (runs_on)";
  - the "Pipeline Structure" description mentions the optional block;
  - the "Send Notification" section states that the step does not run after a failure, with
    the same placeholder correction in its example.
- `README.md`: a short example in the Pipeline Example section.

## Testing

**Validator (pytest)**
- A pipeline with the short form, the full form, and both together passes.
- Each rule fails with a message and a line: `notifications` not a mapping; an unknown key
  such as `on_fail`; `on_failure` missing, not a list, or empty; an entry that is neither a
  string nor a mapping; an empty channel name; a mapping without `channel`; an unknown entry
  key such as `chanel`; `message`, `subject` or `recipient` that is empty or not a string.
- The block does not change what the pipeline compiles to.

**Reader and message (pytest)**
- Both entry forms normalize to the same shape.
- Missing block, invalid YAML, and malformed blocks return an empty list without raising.
- The default message and subject, including the cases with no commit, no branch and no
  failed step.
- A custom message with the three new placeholders and an existing one.

**Sending (pytest, in-memory database, sender stubbed)**
- One send per entry, with the rendered message, subject and recipient, linked to the build.
- An unknown channel and a disabled channel each produce a log line and do not stop the
  other entries.
- A failed delivery produces a log line that does not contain the provider's error, and the
  error is on the delivery row.
- For a Telegram channel, a `<` or `&` in a step or branch name is escaped in the default
  message and in substituted values, and tags the author wrote are kept.
- A pipeline without the block sends nothing.

**Executor (pytest)**
- A build that ends failed triggers sending; one that ends successful or cancelled does not.
- An exception while sending leaves the build's status as `failed` and does not escape.

**Docs (pytest)**
- Every YAML example in the prompt's notifications section passes the validator.

**Manual**
- With a real channel configured, fail a build and confirm the message arrives and a
  delivery row appears.

## Files touched

- `backend/app/services/pipeline_compiler.py` — `_validate_notifications`, docstring.
- `backend/app/services/build_notifications.py` — **new**.
- `backend/app/services/build_executor.py` — one call at the end of a failed build.
- `backend/app/api/v1/ai_assistant.py` — prompt section, structure example, rule.
- `frontend/src/components/pipeline/docs-panel.tsx` — docs section and two text updates.
- `README.md` — example.
- `backend/tests/` — new tests.

## Rollback

No schema, protocol or agent change. Reverting the code makes the validator ignore the
`notifications` key again (unknown top-level keys are not rejected), so pipelines that use it
keep running and simply stop sending.
