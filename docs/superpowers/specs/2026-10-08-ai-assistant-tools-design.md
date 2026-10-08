# AI Assistant Editing Tools — Design

**Date:** 2026-10-08
**Status:** Approved (design)
**Area:** AI pipeline assistant (backend), assistant chat panel (frontend)

## Problem

The pipeline assistant is a single model call with no tools. For every request, including a
one-line change, the model must reproduce the entire pipeline YAML, and the user's only
action is "Apply to editor", which replaces everything without showing what changed.

That has three costs:

- The model cannot check its work. An invalid suggestion goes back to the editor as written.
- Rewriting the whole file invites accidental changes elsewhere in it, and the user cannot
  see them.
- Long pipelines are slow and expensive to regenerate for small edits.

This design gives the assistant tools to read and edit a working copy of the YAML and to
validate it, and replaces "Apply to editor" with a reviewed diff the user accepts or
discards.

## Goals

- The assistant edits the YAML with targeted tools (read, search, replace, insert) instead
  of regenerating it.
- The assistant can run the real pipeline validator on its result before proposing it.
- The user sees a diff of every proposed change and decides whether it reaches the editor.
- The user sees what the assistant is doing while it works.
- Models that cannot call tools keep working as today, and still get the diff review.

## Non-goals

- Accepting or rejecting individual changes within a proposal. A proposal is applied or
  discarded as a whole.
- Showing the diff inside the code editor.
- Streaming the assistant's reply word by word.
- Tools that read notification channels, build history, build logs, or repository files.
  These are a second phase.
- Tools that list secret or variable names. The prompt already includes the project's
  secret and variable names.
- Saving chats, or changing which AI providers are supported or how they are configured.
- Any change to who may use the assistant (`_check_ai_access` is unchanged).

## How it works

1. The browser sends the user's message, the chat history, and the editor's current YAML to
   `POST /api/v1/ai/assistant/stream`, as it sends them to `/ai/assistant` today.
2. The server creates a **working copy** of that YAML, held in memory for this one request.
3. The server runs a loop: it calls the model with the tool definitions; when the model asks
   for tools, the server runs them against the working copy, appends the results, and calls
   the model again; when the model answers with text and no tool calls, the loop ends.
4. After each tool runs, the server streams a **step** event, so the chat can show progress.
5. When the loop ends, the server validates the working copy, builds a **proposal** (the new
   YAML and a diff against the YAML the browser sent), and streams a final **done** event
   with the assistant's reply and the proposal.
6. The chat shows the reply and a review card. **Apply** replaces the editor's content with
   the proposed YAML. **Discard** does nothing. Nothing reaches the editor before Apply.

The tools run on the server because that is where the validator and the user's permissions
are. The working copy is never stored.

## Tools

Line numbers are 1-based and inclusive. Every tool returns text to the model. A tool that
cannot do what was asked returns an error message as its result; it never ends the loop.

### Editing the working copy

| Tool | Arguments | Behavior |
|---|---|---|
| `read_lines` | `start` (default 1), `end` (default last line) | Returns the lines, each prefixed with its number. At most 400 lines per call; a longer range is cut and says so. |
| `search` | `pattern`, `regex` (default false) | Returns each matching line with its number and one line of context either side. Plain search is case-insensitive. At most 50 matches. An invalid regular expression is an error. |
| `replace_text` | `old`, `new` | Replaces one exact piece of text, whitespace included. Fails if `old` is not found, or is found more than once (the error lists the line numbers), or is empty. |
| `replace_lines` | `start`, `end`, `text` | Replaces the line range with `text`. Empty `text` deletes the range. |
| `insert_lines` | `after`, `text` | Inserts `text` after line `after`. `after: 0` inserts at the top. |
| `write_document` | `text` | Replaces the whole working copy. For new pipelines and full rewrites. |

- After a successful edit the tool returns a one-line confirmation and the changed region
  with two lines of context, numbered as they now are, so the model sees the result and the
  new line numbers.
- An edit that would make the working copy larger than 256 KiB (the validator's limit) is
  refused.
- A range outside the document is an error that states how many lines the document has.

### Checking

| Tool | Arguments | Behavior |
|---|---|---|
| `validate` | none | Runs `validate_pipeline_definition` on the working copy. Returns `No problems.` or one line per problem with its line number. |
| `show_diff` | none | Returns a unified diff of everything changed so far, or `No changes yet.` |

### Looking things up

| Tool | Arguments | Behavior |
|---|---|---|
| `reference` | `topic` | Returns the documentation for one topic: a step type (`run`, `http_request`, `kube_apply`, …) or one of `structure`, `artifacts`, `runs_on`, `notifications`. An unknown topic returns the list of valid topics. |
| `list_agents` | none | Returns each registered agent's name, OS, architecture, labels and whether it is online, for writing `runs_on`. Requires the `agents.read` permission; without it the tool says so. |

### The prompt in tool mode

Today's `SYSTEM_PROMPT` stays as it is and remains the prompt for models without tools. The
existing tests that read its sections keep working.

In tool mode the prompt is shorter, because the loop re-sends it on every model call:

- the introduction, the pipeline structure, the placeholders, and the rules that still
  apply;
- a one-line index of the topics `reference` can return, in place of the full step and
  feature sections, which are served by `reference` on demand;
- instructions for the tools: read before editing; prefer `replace_text` for small changes;
  call `validate` before finishing and fix what it reports; do not paste the YAML into the
  reply; end with a short explanation of what changed.

The reference sections are taken from `SYSTEM_PROMPT` by splitting it on its headings, so
there is one source for the documentation.

The project context (secret and variable names) and the repository context are appended in
both modes, as today.

### Limits

- At most 16 tool calls per request, and at most 120 seconds for the whole loop.
- When a limit is reached the loop stops, the proposal is built from the working copy as it
  stands, and the response says the limit was reached so the chat can tell the user.
- Arguments that are not valid JSON, an unknown tool name, and arguments of the wrong type
  are returned to the model as tool errors.

## Models without tool support

Tool mode is used when all of these hold:

- the setting `MEGOOCI_AI_TOOLS_ENABLED` is true (default true; a switch to turn the feature
  off);
- the model is reported by the AI library as supporting tool calls.

Otherwise, and also when the provider rejects a request because it carries tools, the server
falls back to today's behavior for that request: one call with the full prompt, and the YAML
is taken from the reply. The fallback still produces a proposal, by diffing that YAML against
the editor's, so the review card works the same way.

## API

### Request

`AssistantRequest` is unchanged.

### Response

`POST /ai/assistant` returns, and the `done` event of `/ai/assistant/stream` carries:

```json
{
  "reply": "I added an http_request step after the deploy step…",
  "yaml": "name: deploy\n…",
  "mode": "tools",
  "limit_reached": false,
  "steps": [
    {"tool": "read_lines", "label": "Read lines 1–42", "ok": true},
    {"tool": "insert_lines", "label": "Inserted 6 lines after line 31", "ok": true},
    {"tool": "validate", "label": "Validated · no problems", "ok": true}
  ],
  "proposal": {
    "yaml": "name: deploy\n…",
    "added": 6,
    "removed": 0,
    "problems": [],
    "hunks": [
      {
        "old_start": 29,
        "new_start": 29,
        "lines": [
          {"kind": "context", "old": 31, "new": 31, "text": "      - run: ./deploy.sh"},
          {"kind": "add", "old": null, "new": 32, "text": "      - name: announce"}
        ]
      }
    ]
  }
}
```

- `proposal` is `null` when the working copy is unchanged (the assistant only answered a
  question) or, in fallback mode, when the reply contains no YAML.
- `yaml` is kept for compatibility and equals `proposal.yaml`, or `null`.
- `mode` is `"tools"` or `"legacy"`.
- `problems` are the validator's errors for the proposed YAML: `{"message", "line"}`.
- `hunks` are built on the server with three lines of context. `kind` is `context`, `add`
  or `remove`.

### Stream events

`/ai/assistant/stream` sends server-sent events, each a JSON object:

| `type` | Fields | When |
|---|---|---|
| `step` | `tool`, `label`, `ok` | After each tool call. |
| `done` | the response object above | Once, at the end. |
| `error` | `detail` | Once, instead of `done`, when the request fails. |

The `token` events the endpoint sends today are removed; nothing consumes them.

Step labels are written by the server from the tool and its outcome. They never include the
YAML's content beyond a search pattern the model chose.

## Frontend

The chat stays in its side sheet. The panel switches from `/ai/assistant` to the streaming
endpoint.

### While the assistant works

The "Thinking…" line is replaced by a list that grows as step events arrive:

```
(ai)  Working…
      ✓ Read lines 1–42
      ✓ Searched "deploy" · 3 matches
      ✓ Inserted 6 lines after line 31
      ✓ Validated · no problems
```

A failed step shows `✕` in place of `✓`. When the reply arrives the list collapses to one
line, "5 steps", which expands on click.

### The review card

Shown under the assistant's reply whenever the response has a proposal.

```
┌─ Proposed change ───────────── +6  −0 · ✓ valid ─┐
│  lines 29–37                                      │
│   29      steps:                                  │
│   30        - name: apply manifests               │
│   31          run: ./deploy.sh                    │
│ + 32        - name: announce                      │
│ + 33          http_request:                       │
│ + 34            url: ${{ secrets.TEAMS_WEBHOOK }} │
│   35    - name: smoke-test                        │
│                                                   │
│  [ Discard ]                 [ Apply to editor ]  │
└───────────────────────────────────────────────────┘
```

- Added lines are green with `+`, removed lines red with `−`, context lines plain. Each line
  shows its line number: the new number for added and context lines, the old one for removed
  lines.
- Hunks are separated by a "lines N–M" heading. Long lines scroll sideways inside the card;
  the card never widens the sheet. More than 400 diff lines are cut with a note.
- The header shows the added and removed counts and the validation state: `✓ valid`, or
  `⚠ N problems` followed by each problem and its line.
- **Apply to editor** stays available when there are problems; the user may want to fix them
  by hand.

States of the card:

| State | Appearance |
|---|---|
| Pending | As drawn. |
| Applied | Collapses to `Applied to editor ✓ · +6 −0` with an **Undo** button. |
| Discarded | Collapses to `Discarded · +6 −0`. |
| Stale | The editor's content differs from the YAML the request was sent with. The card shows "The editor changed since this was proposed" and the button reads **Apply anyway**. |

- **Undo** puts back the editor content from just before Apply. It is offered until the
  editor changes again or another proposal is applied.
- Applying is a single editor change, so the editor's own undo also reverts it.
- When the response says a limit was reached, a line above the card says the assistant
  stopped early and the proposal may be incomplete.
- The "Generated YAML" block with its Copy button is removed. The card has a **Copy YAML**
  action in its header instead.

### History

The history sent with later messages contains the user's messages and the assistant's
replies as text, as today. Tool calls from earlier turns are not replayed.

## Error handling

| Situation | Result |
|---|---|
| The AI provider fails (authentication, bad request, unreachable) | The same 502 errors as today from `/ai/assistant`; an `error` event from the stream. |
| The provider rejects a request because it carries tools | Fall back to the single call for this request. |
| A tool raises unexpectedly | The model receives "internal error" as that tool's result and the exception is logged; the loop continues. |
| The model returns neither text nor tool calls | The loop ends with a generic reply; a proposal is still built if the working copy changed. |
| The stream is cut off mid-request | The chat shows an error message in place of the reply; the steps received so far stay visible. |
| The user closes the sheet or sends again while a request runs | The request is aborted in the browser. |

## Security and cost

- The tools can read and change only the working copy the user sent. `list_agents` is the
  only tool that reads server data, and it checks the user's permission.
- No tool returns a secret's value. The existing project context decides what the prompt
  contains, as today.
- Each request can make several model calls. The server logs the number of model calls,
  tool calls and reported token usage per request, and `MEGOOCI_AI_TOOLS_ENABLED` turns the
  loop off.

## Testing

**Working copy (pytest)**
- Each tool's normal behavior, numbering and returned context.
- `replace_text`: not found, found twice (line numbers listed), empty `old`, multi-line
  text, text at the start and end of the document.
- Ranges: out of bounds, `start` after `end`, `insert_lines` at 0 and after the last line,
  deleting a range, an empty document, a document with and without a final newline, CRLF
  input.
- The size limit, the 400-line read cap, the 50-match search cap, an invalid regular
  expression.

**Diff (pytest)**
- No change gives no proposal. Additions, removals and replacements give the right counts,
  line numbers and hunk boundaries. A new document (empty original) is all additions.

**Loop (pytest, with a scripted fake model)**
- A sequence of tool calls followed by a final answer; step events in order.
- Unknown tool, invalid JSON arguments, wrong argument types, and a tool that raises.
- The tool-call limit and the time limit.
- A provider error that rejects tools triggers the fallback.

**Endpoints (pytest, AI library stubbed)**
- Tool mode and fallback mode for both endpoints; the event sequence of the stream.
- `MEGOOCI_AI_TOOLS_ENABLED=false` uses the fallback.
- `list_agents` with and without the permission.

**Prompt (pytest)**
- `reference` returns each step type's section and the other topics; the tool-mode prompt
  names every tool and does not contain the step sections; the existing prompt tests pass
  unchanged.

**Frontend**
- `npx tsc --noEmit`, and a manual pass through each card state, a long diff, and a narrow
  (mobile) sheet.

## Files touched

- `backend/app/services/assistant/` — **new** package: `document.py` (working copy and
  editing operations), `diff.py` (proposal and hunks), `reference.py` (topics from the
  prompt), `tools.py` (tool definitions and dispatch), `loop.py` (the model loop).
- `backend/app/api/v1/ai_assistant.py` — both endpoints use the loop; response model;
  fallback; tool-mode prompt.
- `backend/app/config.py` — `MEGOOCI_AI_TOOLS_ENABLED`.
- `frontend/src/lib/api.ts` — response types and a streaming call.
- `frontend/src/components/pipeline/ai-assistant-panel.tsx` — steps, streaming, card states.
- `frontend/src/components/pipeline/ai-proposal-card.tsx` — **new**: the review card.
- `.env.example`, `README.md` — the new setting.
- `backend/tests/` — new tests.

## Rollback

No schema change. Setting `MEGOOCI_AI_TOOLS_ENABLED=false` returns the assistant to one model
call per request while keeping the review card. Reverting the code restores the previous
panel and endpoints.
