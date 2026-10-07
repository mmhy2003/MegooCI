# HTTP Request Step — Design

**Date:** 2026-10-07
**Status:** Approved (design)
**Area:** Pipeline YAML, build agent, pipeline docs, AI assistant prompt

## Problem

A pipeline cannot call an external system over HTTP except by shelling out to `curl` in a
`run` step. That is fragile: it depends on `curl` being installed on the agent, JSON bodies
have to survive shell quoting, and the command line (URL, tokens, payload) ends up in the
build log.

The existing `notify` step does not cover this. It sends only through admin-configured
channels of three fixed types (email, Slack, Telegram).

This design adds an `http_request` step that sends one HTTP request to any URL, with a body
the pipeline author writes freely, and fails the build when the receiver does not answer as
expected.

## Goals

- Call any HTTP endpoint from a pipeline: chat webhooks, deployment systems, ticketing
  systems, the author's own services.
- Make no assumption about the payload's structure. The author writes the body.
- Keep secrets (URL, tokens, payload) out of the build log.
- Reach systems on the agent's network, including ones with self-signed certificates.
- Document the step in the in-app pipeline docs, the AI assistant prompt, and the README.

## Non-goals

- Using the response in later steps. Steps have no output mechanism today.
- Form-encoded bodies, multipart bodies, and file uploads.
- Provider presets (Teams, Discord, and so on) and a webhook channel type for `notify`.
  Either can be added later on top of this step.
- Sending the request from the MegooCI server.
- Following redirects.
- Routing the step only to agents that support it.

## Approach

Three shapes were considered:

- **A. Generic `http_request` step (chosen).** Free-form `json` or `body`. Covers any
  receiver; the author must know the receiver's payload.
- **B. Provider presets.** Friendlier for the providers we build, but every new provider is
  new code, forever.
- **C. A webhook channel type for `notify`.** Centralizes URLs in Settings, but only admins
  can add a target and the payload is less flexible per pipeline.

The step is named `http_request`, not "webhook", because `wait_webhook` already exists and is
an inbound gate.

### Where the request is sent from

The **build agent** sends the request.

- Agents can reach internal systems that the server cannot.
- A pipeline author can already run `curl` on the agent, so the step adds no new access.
  Sending from the server would let any pipeline author make the server call addresses on
  its own network (database, Redis, cloud metadata).
- The cost is a Go change, and agents must be updated before pipelines can use the step.

## YAML

```yaml
- name: announce-deploy
  http_request:
    url: ${{ secrets.TEAMS_WEBHOOK_URL }}
    method: POST
    headers:
      Authorization: Bearer ${{ secrets.DEPLOY_API_TOKEN }}
    json:
      text: "Build #${{ build.number }} of ${{ pipeline.name }} deployed"
      branch: ${{ build.branch }}
      tags: [ci, production]
    timeout: 30
    retries: 2
    expect_status: [200, 202]
```

```yaml
- http_request:
    url: https://legacy.example.com/hook
    headers:
      Content-Type: application/xml
    body: |
      <deploy><build>${{ build.number }}</build></deploy>
```

### Fields

| Field | Required | Default | Rules |
|---|---|---|---|
| `url` | yes | — | Non-empty string. After placeholders are replaced it must be an absolute `http://` or `https://` URL with a host. |
| `method` | no | `POST` | One of `GET`, `POST`, `PUT`, `PATCH`, `DELETE`. Case-insensitive. |
| `headers` | no | — | Mapping of header name to a string, number or boolean. Values are sent as strings. |
| `json` | no | — | A mapping or a list. Sent as JSON. |
| `body` | no | — | A string. Sent exactly as written. |
| `timeout` | no | `30` | Seconds per attempt. A number greater than 0 and at most 300. |
| `retries` | no | `0` | Extra attempts after the first. An integer from 0 to 5. |
| `expect_status` | no | any 2xx | An integer from 100 to 599, or a non-empty list of them. |
| `verify_tls` | no | `true` | A boolean. `false` accepts any server certificate. |

- `json` and `body` cannot both be present. Neither is required: a request may have no body.
- With `json`, the agent sets `Content-Type: application/json` unless `headers` already sets
  a `Content-Type` (matched case-insensitively).
- With `body`, no `Content-Type` is added. The author sets it in `headers`.
- The step composes with step-level `name`, `when` and `env` like every other step.

### Placeholders and types

The server replaces `${{ secrets.X }}`, `${{ env.X }}`, `${{ build.X }}`,
`${{ pipeline.X }}` and `${{ project.X }}` in every string of the step, including strings
nested anywhere inside `json`, before the step is dispatched. This is the existing
interpolation and needs no change.

A placeholder always produces a string, so `count: ${{ build.number }}` sends `"42"`. Literal
YAML numbers, booleans and nulls inside `json` keep their types. A receiver that needs a
placeholder's value as a number uses `body` with hand-written JSON:

```yaml
body: '{"build": ${{ build.number }}, "ok": true}'
```

## Validation (server)

`backend/app/services/pipeline_compiler.py`:

- Add `http_request` to `STEP_TYPE_KEYS`.
- In `_validate_step`, enforce every rule in the Fields table that can be checked without
  resolving placeholders:
  - `http_request` must be a mapping.
  - `url` is required and must be a non-empty string. When it contains no `${{`, it must
    start with `http://` or `https://`.
  - `method`, when present, must be one of the five allowed values.
  - `headers`, when present, must be a mapping whose values are strings, numbers or
    booleans.
  - `json` and `body` must not both be present; `json` must be a mapping or list; `body`
    must be a string.
  - `timeout`, `retries`, `expect_status` and `verify_tls` must satisfy their type and range
    rules. A boolean is never accepted where a number is required.
- Errors use the existing message format and line attribution, so they appear in the editor,
  in `POST /pipelines/validate`, in the MCP `validate_pipeline_yaml` tool, and block
  `trigger_build`, exactly like other steps.

No server-side handler is registered. Like `kube_apply`, the step compiles to
`{"step_type": "http_request", "config": {...}}` and is dispatched to the agent through the
existing path. No change to `build_executor.py` is needed: every step type outside the
server-only set already goes to the agent.

## Execution (agent)

A new file `agent/internal/executor/http_request.go`, with one dispatch line in
`agent/internal/executor/local.go` beside the other native step types. It uses only the Go
standard library.

1. Read and check the config. The agent re-checks what the server could not: the final URL
   must be absolute `http`/`https` with a host. A bad config fails the step before anything
   is sent.
2. Build the request: method, URL, headers, and the body from `json` (encoded as JSON) or
   `body` (sent as is).
3. Send it with a client that:
   - applies `timeout` to each attempt,
   - does **not** follow redirects (a 3xx is returned as the response),
   - skips certificate verification only when `verify_tls` is `false`,
   - is bound to the step's context, so cancelling the build aborts the request.
4. Decide the outcome:
   - Status matches `expect_status` (or is 2xx by default): success.
   - Otherwise, or on a network error or timeout: retry if the failure is retryable and
     attempts remain; else fail.

### Retries

- Retryable: network errors, timeouts, any 5xx status, and 429.
- Not retryable: every other status, including 4xx and 3xx.
- A status that matches `expect_status` is success even if it is 5xx or 429.
- The wait before retry *n* is `2^(n-1)` seconds: 1, 2, 4, 8, 16. The wait is interrupted by
  cancellation.
- The request body is re-sent in full on each attempt.

### Result

- Success: exit code 0.
- Failure: exit code 1, with one stderr line stating why, for example
  `unexpected status 500 (expected 2xx)`, `unexpected status 302 (redirects are not
  followed)`, or `request failed: context deadline exceeded`.
- Cancellation: status `cancelled`, like other agent steps.

## Logging

Agent log lines are not passed through the server's secret masking, so the step never prints
what could be secret.

```
POST https://hooks.example.com/… (attempt 1 of 3)
200 OK in 182 ms
Response (first 2000 characters):
{"ok": true}
```

- The URL is shown as scheme and host only, followed by `/…`. Path, query and any user-info
  are never printed, because webhook URLs often carry the secret in the path.
- Request headers and the request body are never printed.
- The response body is printed up to its first 2000 characters. Before printing, every
  occurrence of a request header value or of the full request URL is replaced with `***`, in
  case the receiver echoes them. Header values shorter than 4 characters are not masked, to
  avoid mangling the output.
- At most 64 KiB of the response body is read; the rest is discarded.
- A response with no body prints no response section.
- When `verify_tls` is `false`, one warning line says certificate verification is disabled.
- Each failed attempt that will be retried prints the reason and the wait.

## Agent compatibility

An agent built before this change does not know `http_request` and fails the step with
`empty command for step type "http_request"`. That message cannot be changed on agents
already deployed. The docs state that the step needs an agent built from this release or
later. Nothing is added to route the step only to capable agents.

## Documentation

- `frontend/src/components/pipeline/docs-panel.tsx`: a new "HTTP Request" section after
  "Kubernetes Apply", with a description and a YAML example covering `json`, `headers`,
  `retries` and `expect_status`.
- `backend/app/api/v1/ai_assistant.py`: a new `http_request` section in the system prompt,
  with the fields, both body forms, and these rules for the assistant:
  - use `http_request` to call an external URL; use `notify` for a channel configured in
    Notification Channels;
  - always take URLs that contain tokens, and all credentials, from `${{ secrets.X }}`;
  - placeholders are strings; use `body` when a number or boolean must come from a
    placeholder.
- `README.md`: add `http_request` to the list of built-in step types, with a short example.
- The module docstring of `pipeline_compiler.py` lists the new step.

## Testing

**Backend (pytest, `backend/tests/test_pipeline_validation.py` and
`test_pipeline_compiler.py`)**
- A valid step with `json`, and one with `body`, pass validation and compile to
  `step_type == "http_request"` with the mapping as config.
- Each rule fails with a message naming the stage and step: not a mapping; missing `url`;
  literal `url` without `http(s)://`; a `url` made of a placeholder is accepted; unknown
  `method`; `headers` not a mapping or with a nested value; both `json` and `body`; `json`
  a scalar; `body` not a string; `timeout` zero, negative, above 300, or a boolean;
  `retries` negative, above 5, a float, or a boolean; `expect_status` out of range, empty
  list, or a string; `verify_tls` not a boolean.
- A validation error carries the line of the step.

**Agent (Go tests against `httptest` servers)**
- `json` is sent as JSON with `Content-Type: application/json`; an explicit `Content-Type`
  header is kept.
- `body` is sent unchanged with no added `Content-Type`.
- Method and headers reach the server; the default method is POST.
- 2xx succeeds; 404 fails without a retry; `expect_status` accepts a listed non-2xx status
  and rejects an unlisted 2xx.
- 500 then 200 succeeds with `retries: 1`; 500 with `retries: 0` fails; 429 is retried.
- A slow server fails with a timeout; a cancelled context ends the step as cancelled,
  including during the retry wait.
- A 302 is not followed and fails.
- A TLS server with a self-signed certificate fails by default and succeeds with
  `verify_tls: false`.
- Invalid URL (no scheme, non-http scheme, no host) fails before sending.
- The logs never contain the URL path or query, a header value, or the request body; a
  header value echoed in the response is masked.
- A response longer than 2000 characters is truncated in the log.

**Manual**
- Run a pipeline with an `http_request` step against a request-inspection endpoint from an
  updated agent, and confirm the request and the log output.

## Files touched

- `backend/app/services/pipeline_compiler.py` — step type, validation, docstring.
- `backend/tests/test_pipeline_validation.py`, `backend/tests/test_pipeline_compiler.py` —
  new tests.
- `agent/internal/executor/http_request.go` — **new**.
- `agent/internal/executor/http_request_test.go` — **new**.
- `agent/internal/executor/local.go` — one dispatch branch.
- `backend/app/api/v1/ai_assistant.py` — prompt section.
- `frontend/src/components/pipeline/docs-panel.tsx` — docs section.
- `README.md` — step list and example.

## Rollback

No schema or protocol change. Reverting the server change makes the validator reject
`http_request` again; pipelines that use it stop triggering until the step is removed.
Updated agents keep the capability harmlessly.
