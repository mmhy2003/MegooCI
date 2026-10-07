# HTTP Request Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `http_request` pipeline step that sends one HTTP request from the build agent to any URL, with a body the pipeline author writes freely, and document it in the in-app docs, the AI assistant prompt and the README.

**Architecture:** The server only validates and compiles the step; like `kube_apply` it has no server-side handler, and the existing executor path interpolates placeholders and dispatches it to the agent. The Go agent gets one new native step runner built on the standard library `net/http`, which sends the request, applies retries and status checks, and writes secret-safe log lines.

**Tech Stack:** Python 3 / pytest (validator in `backend/app/services/pipeline_compiler.py`), Go 1.22 standard library with `net/http/httptest` for tests (agent), Next.js + TypeScript (docs panel).

**Spec:** `docs/superpowers/specs/2026-10-07-http-request-step-design.md`

## Global Constraints

- **Branch:** all work happens on `feat/http-request-step`. Never commit to `main`.
- **Commits:** conventional style (`feat(pipeline): …`, `feat(agent): …`, `docs(pipeline): …`). End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Step name:** `http_request`. Never "webhook": `wait_webhook` already exists and is an inbound gate.
- **Fields, exactly these nine:** `url`, `method`, `headers`, `json`, `body`, `timeout`, `retries`, `expect_status`, `verify_tls`. Any other field is a validation error.
- **Defaults and limits:** `method` defaults to `POST` and must be one of `GET`, `POST`, `PUT`, `PATCH`, `DELETE`; `timeout` defaults to 30 seconds, must be greater than 0 and at most 300; `retries` defaults to 0, a whole number from 0 to 5; `expect_status` defaults to any 2xx, each code from 100 to 599; `verify_tls` defaults to `true`.
- **`json` and `body` are mutually exclusive** and both optional.
- **The agent sends the request.** No server-side handler is registered, and `backend/app/services/build_executor.py` is not changed.
- **No new dependencies** in the agent (`go.mod` is unchanged) or the backend.
- **Never log** the URL beyond scheme and host, any request header, or the request body. Error messages obey the same rule.
- **Redirects are not followed.**
- **Retries** happen only for network errors, timeouts, 5xx and 429, with waits of 1, 2, 4, 8, 16 seconds.
- **Backend tests:** run from `backend/` with `./.venv/Scripts/python.exe -m pytest`.
- **Agent tests:** run from `agent/` with `go test ./internal/executor/ -count=1`.
- **`gofmt`:** check only the files a task creates. On this Windows checkout `gofmt -l` also lists pre-existing files because of CRLF line endings; that is not caused by this plan.
- **Frontend gate:** `npx tsc --noEmit` from `frontend/`. There is no frontend unit-test harness, and `npm run lint` is broken; do not use it.
- **Paths in commands:** `git` commands use paths relative to the repository root. Commands are written for Git Bash.

## Review Focus

Inputs the spec implies but does not spell out. Each has a test in the task that owns the code.

1. **A misspelled field such as `json_body` or `header`.** Expected: a validation error naming the field, not a request silently sent without its body. Pinned in Task 1 (`test_misspelled_field_is_reported_not_silently_dropped`).
2. **A `url` whose secret does not exist, so the placeholder resolves to an empty string.** Expected: the step fails with a message that points at the secret, and nothing is sent. Pinned in Task 2 (`TestHTTPRequestRejectsBadConfigBeforeSending`, case "empty url (unresolved secret)").
3. **A network failure.** Go's HTTP client quotes the full URL in its error text. Expected: the log shows the cause without the URL path. Pinned in Task 2 (`TestHTTPRequestConnectionRefusedIsRetried`).
4. **A very large or binary response.** Expected: the step neither stalls nor floods the build log, and the log stays valid text. Pinned in Task 2 (`TestHTTPRequestHugeResponseDoesNotStallOrFlood`, `TestHTTPRequestBinaryResponseIsLoggedAsValidText`).
5. **Payload text containing `<`, `>`, `&` or non-ASCII characters** (chat formats use `<url|text>`). Expected: sent as written, not as `<` escapes. Pinned in Task 2 (`TestHTTPRequestSendsJSONBody`).

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/services/pipeline_compiler.py` | Modify | Step type key, `_validate_http_request`, docstring. |
| `backend/tests/test_http_request_step.py` | Create | Validation, compilation and interpolation tests. |
| `agent/internal/executor/http_request.go` | Create | Config parsing, request sending, retries, logging. |
| `agent/internal/executor/http_request_test.go` | Create | Tests against local `httptest` servers. |
| `agent/internal/executor/local.go` | Modify | One dispatch branch. |
| `backend/app/api/v1/ai_assistant.py` | Modify | Prompt section, placeholder list, rule 7. |
| `backend/tests/test_http_request_docs.py` | Create | The prompt's examples pass the validator. |
| `frontend/src/components/pipeline/docs-panel.tsx` | Modify | "HTTP Request" docs section. |
| `README.md` | Modify | Step list and example. |

The spec names `test_pipeline_validation.py` and `test_pipeline_compiler.py` for the backend tests. This plan uses one dedicated file instead, as `test_pipeline_compiler.py` does for `kube_apply`.

---

### Task 1: Validate and compile the step on the server

**Files:**
- Modify: `backend/app/services/pipeline_compiler.py` (module docstring, `STEP_TYPE_KEYS`, a new helper above `_validate_step`, one branch inside `_validate_step`)
- Test: `backend/tests/test_http_request_step.py` (new)

**Interfaces:**
- Produces: `STEP_TYPE_KEYS` contains `"http_request"`.
- Produces: module constants `HTTP_REQUEST_METHODS`, `HTTP_REQUEST_FIELDS` (a tuple of the nine field names, in the order listed in Global Constraints), `HTTP_REQUEST_MAX_TIMEOUT = 300`, `HTTP_REQUEST_MAX_RETRIES = 5`.
- Produces: `_validate_http_request(value: Any, prefix: str) -> list[str]`, where `prefix` is `"Stage '<name>', step <index>"`. Every message starts with `f"{prefix}: 'http_request' "`.
- Produces: a valid step compiles to `{"name": ..., "step_type": "http_request", "config": <the mapping as written>, ...}` through the existing `_detect_step_type`, with placeholders untouched.

**How the pieces already work (no change needed):**
- `validate_pipeline(yaml)` returns error messages; `validate_pipeline_definition(yaml)` returns `PipelineError` objects whose `line` is the step mapping's 1-based line.
- `_detect_step_type` returns `(key, dict(value))` for any mapping-valued step type, so no compile change is needed.
- `interpolate_value` in `app/services/step_actions/interpolation.py` already replaces placeholders recursively through nested mappings and lists and leaves non-strings unchanged.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_http_request_step.py`:

```python
"""Validation and compilation tests for the http_request step type."""

import pytest

from app.services.pipeline_compiler import (
    compile_to_build_graph,
    parse_yaml_pipeline,
    validate_pipeline,
    validate_pipeline_definition,
)


def _pipeline(step_block: str) -> str:
    """Wrap a YAML step block (indented 6 spaces) in a minimal pipeline."""
    return (
        "name: test\n"
        "stages:\n"
        "  - name: notify\n"
        "    steps:\n" + step_block
    )


def _step(fields: str) -> str:
    """A pipeline whose only step is http_request with the given field lines
    (each indented 10 spaces)."""
    return _pipeline("      - http_request:\n" + fields)


URL = "          url: https://hooks.example.com/x\n"

VALID_JSON = _pipeline(
    "      - name: announce\n"
    "        http_request:\n"
    "          url: ${{ secrets.TEAMS_WEBHOOK_URL }}\n"
    "          method: POST\n"
    "          headers:\n"
    "            Authorization: Bearer ${{ secrets.DEPLOY_API_TOKEN }}\n"
    "            X-Attempt: 2\n"
    "          json:\n"
    "            text: \"Build #${{ build.number }} deployed\"\n"
    "            count: 3\n"
    "            ok: true\n"
    "            tags: [ci, production]\n"
    "            nested:\n"
    "              branch: ${{ build.branch }}\n"
    "          timeout: 30\n"
    "          retries: 2\n"
    "          expect_status: [200, 202]\n"
    "          verify_tls: false\n"
)

VALID_BODY = _step(
    "          url: https://legacy.example.com/hook\n"
    "          method: put\n"
    "          headers:\n"
    "            Content-Type: application/xml\n"
    "          body: |\n"
    "            <deploy><build>${{ build.number }}</build></deploy>\n"
    "          timeout: 2.5\n"
    "          expect_status: 204\n"
)


def test_valid_json_step_passes():
    assert validate_pipeline(VALID_JSON) == []


def test_valid_body_step_passes():
    assert validate_pipeline(VALID_BODY) == []


def test_minimal_step_needs_only_a_url():
    assert validate_pipeline(_step(URL)) == []


def test_json_may_be_a_list():
    assert validate_pipeline(_step(URL + "          json: [a, b]\n")) == []


def test_url_made_of_a_placeholder_is_accepted():
    assert validate_pipeline(_step("          url: ${{ secrets.HOOK_URL }}\n")) == []


def test_url_with_a_placeholder_inside_is_accepted():
    assert validate_pipeline(_step("          url: ${{ env.BASE }}/hooks/deploy\n")) == []


def test_compiles_to_http_request_step():
    stages = compile_to_build_graph(parse_yaml_pipeline(VALID_JSON))
    step = stages[0]["steps"][0]
    assert step["step_type"] == "http_request"
    assert step["name"] == "announce"
    config = step["config"]
    assert config["url"] == "${{ secrets.TEAMS_WEBHOOK_URL }}"
    assert config["method"] == "POST"
    assert config["headers"] == {
        "Authorization": "Bearer ${{ secrets.DEPLOY_API_TOKEN }}",
        "X-Attempt": 2,
    }
    assert config["json"] == {
        "text": "Build #${{ build.number }} deployed",
        "count": 3,
        "ok": True,
        "tags": ["ci", "production"],
        "nested": {"branch": "${{ build.branch }}"},
    }
    assert config["timeout"] == 30
    assert config["retries"] == 2
    assert config["expect_status"] == [200, 202]
    assert config["verify_tls"] is False


def test_placeholders_inside_json_are_replaced_and_literals_keep_their_types():
    """The server interpolates the whole step config before dispatch; this
    pins that it reaches nested json values and leaves non-strings alone."""
    from app.services.step_actions.interpolation import interpolate_value

    stages = compile_to_build_graph(parse_yaml_pipeline(VALID_JSON))
    config = interpolate_value(
        stages[0]["steps"][0]["config"],
        {"TEAMS_WEBHOOK_URL": "https://hooks.example.com/abc", "DEPLOY_API_TOKEN": "tok"},
        {},
        {"build": {"number": "42", "branch": "main"}},
    )
    assert config["url"] == "https://hooks.example.com/abc"
    assert config["headers"]["Authorization"] == "Bearer tok"
    assert config["json"]["text"] == "Build #42 deployed"
    assert config["json"]["nested"] == {"branch": "main"}
    assert config["json"]["count"] == 3 and config["json"]["ok"] is True
    assert config["json"]["tags"] == ["ci", "production"]


@pytest.mark.parametrize(
    "step_block, expected",
    [
        ("      - http_request: https://example.com/x\n", "'http_request' must be a mapping"),
        ("      - http_request:\n          method: POST\n", "'http_request' requires 'url'"),
        ("      - http_request:\n          url: \"\"\n", "'http_request' requires 'url'"),
        ("      - http_request:\n          url: 42\n", "'http_request' requires 'url'"),
        (
            "      - http_request:\n          url: hooks.example.com/x\n",
            "url must start with http:// or https://",
        ),
        (
            "      - http_request:\n          url: ftp://example.com/x\n",
            "url must start with http:// or https://",
        ),
    ],
)
def test_step_shape_and_url_rules(step_block, expected):
    errors = validate_pipeline(_pipeline(step_block))
    assert any(expected in e for e in errors), errors


@pytest.mark.parametrize(
    "fields, expected",
    [
        ("          method: TRACE\n", "method must be one of: GET, POST, PUT, PATCH, DELETE"),
        ("          method: 5\n", "method must be one of"),
        ("          headers: \"X-A: b\"\n", "headers must be a mapping"),
        ("          headers:\n            X-A: [1, 2]\n", "header names must be strings and values"),
        ("          headers:\n            X-A:\n", "header names must be strings and values"),
        ("          json: {a: 1}\n          body: x\n", "either 'json' or 'body', not both"),
        ("          json: just text\n", "json must be a mapping or a list"),
        ("          json:\n", "json must be a mapping or a list"),
        ("          body: {a: 1}\n", "body must be a string"),
        ("          body: 5\n", "body must be a string"),
        ("          timeout: 0\n", "timeout must be a number greater than 0 and at most 300"),
        ("          timeout: -1\n", "timeout must be a number"),
        ("          timeout: 301\n", "timeout must be a number"),
        ("          timeout: true\n", "timeout must be a number"),
        ("          timeout: \"30\"\n", "timeout must be a number"),
        ("          retries: -1\n", "retries must be a whole number from 0 to 5"),
        ("          retries: 6\n", "retries must be a whole number"),
        ("          retries: 1.5\n", "retries must be a whole number"),
        ("          retries: true\n", "retries must be a whole number"),
        ("          expect_status: 99\n", "expect_status must be a status code"),
        ("          expect_status: 600\n", "expect_status must be a status code"),
        ("          expect_status: []\n", "expect_status must be a status code"),
        ("          expect_status: \"200\"\n", "expect_status must be a status code"),
        ("          expect_status: [200, ok]\n", "expect_status must be a status code"),
        ("          expect_status: true\n", "expect_status must be a status code"),
        ("          verify_tls: \"no\"\n", "verify_tls must be true or false"),
        ("          verify_tls: 0\n", "verify_tls must be true or false"),
    ],
)
def test_field_rules(fields, expected):
    errors = validate_pipeline(_step(URL + fields))
    assert any(expected in e for e in errors), errors


def test_boundary_values_are_accepted():
    fields = (
        URL
        + "          timeout: 300\n"
        + "          retries: 5\n"
        + "          expect_status: [100, 599]\n"
    )
    assert validate_pipeline(_step(fields)) == []
    assert validate_pipeline(_step(URL + "          retries: 0\n")) == []


def test_misspelled_field_is_reported_not_silently_dropped():
    """A typo such as json_body would otherwise send the request with no body."""
    errors = validate_pipeline(_step(URL + "          json_body: {a: 1}\n          header: {}\n"))
    assert any("unknown field(s): header, json_body" in e for e in errors), errors


def test_error_names_the_stage_and_step_and_carries_the_step_line():
    bad = _pipeline(
        "      - run: echo first\n"
        "      - http_request:\n"
        "          method: POST\n"
    )
    errors = validate_pipeline_definition(bad)
    match = [e for e in errors if "requires 'url'" in e.message]
    assert match, errors
    assert match[0].message.startswith("Stage 'notify', step 1: ")
    assert match[0].line == 6  # the http_request step's mapping line


def test_http_request_cannot_be_combined_with_another_action():
    errors = validate_pipeline(_pipeline("      - run: echo hi\n        http_request:\n" + URL))
    assert any("multiple action types" in e for e in errors), errors


def test_step_level_when_and_name_still_work():
    yaml_doc = _pipeline(
        "      - name: on-main\n"
        "        when:\n"
        "          branch: main\n"
        "        http_request:\n" + URL
    )
    assert validate_pipeline(yaml_doc) == []
    step = compile_to_build_graph(parse_yaml_pipeline(yaml_doc))[0]["steps"][0]
    assert step["name"] == "on-main"
    assert step["when"] == {"branch": "main"}
    assert step["step_type"] == "http_request"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_http_request_step.py -q`
Expected: `46 failed`. Every test fails because the validator does not know `http_request` yet (valid steps are reported as "must have one of: …", and the step compiles to an empty `run`).

- [ ] **Step 3: Register the step type**

In `backend/app/services/pipeline_compiler.py`, add one line to the module docstring after the `kube_apply` line:

```python
- kube_apply (apply Kubernetes manifests and wait for rollout)
- http_request (send an HTTP request to an external system)
```

and one entry to `STEP_TYPE_KEYS` after `"kube_apply",`:

```python
    "kube_apply",
    "http_request",
    "wait_webhook",
```

- [ ] **Step 4: Add the validation helper**

In the same file, insert this block immediately above `def _validate_step(`:

```python
HTTP_REQUEST_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
HTTP_REQUEST_FIELDS = (
    "url",
    "method",
    "headers",
    "json",
    "body",
    "timeout",
    "retries",
    "expect_status",
    "verify_tls",
)
HTTP_REQUEST_MAX_TIMEOUT = 300
HTTP_REQUEST_MAX_RETRIES = 5


def _is_number(value: Any) -> bool:
    """A real number. YAML booleans are ints in Python and must not count."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_status_code(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599


def _validate_http_request(value: Any, prefix: str) -> list[str]:
    """Validate an ``http_request`` step. The agent sends the request; it
    re-checks the URL once placeholders have been replaced."""
    if not isinstance(value, dict):
        return [f"{prefix}: 'http_request' must be a mapping"]

    errors: list[str] = []

    # A mistyped field (``json_body``, ``header``) would otherwise be dropped
    # silently and the request sent without it.
    unknown = sorted(str(k) for k in value if k not in HTTP_REQUEST_FIELDS)
    if unknown:
        errors.append(
            f"{prefix}: 'http_request' has unknown field(s): {', '.join(unknown)} "
            f"(allowed: {', '.join(HTTP_REQUEST_FIELDS)})"
        )

    url = value.get("url")
    if not isinstance(url, str) or not url.strip():
        errors.append(f"{prefix}: 'http_request' requires 'url'")
    elif "${{" not in url and not url.strip().lower().startswith(("http://", "https://")):
        errors.append(f"{prefix}: 'http_request' url must start with http:// or https://")

    method = value.get("method")
    if method is not None and (
        not isinstance(method, str) or method.strip().upper() not in HTTP_REQUEST_METHODS
    ):
        errors.append(
            f"{prefix}: 'http_request' method must be one of: {', '.join(HTTP_REQUEST_METHODS)}"
        )

    headers = value.get("headers")
    if headers is not None:
        if not isinstance(headers, dict):
            errors.append(f"{prefix}: 'http_request' headers must be a mapping")
        elif not all(
            isinstance(k, str) and isinstance(v, (str, int, float, bool))
            for k, v in headers.items()
        ):
            errors.append(
                f"{prefix}: 'http_request' header names must be strings and values "
                f"must be strings, numbers or booleans"
            )

    has_json = "json" in value
    has_body = "body" in value
    if has_json and has_body:
        errors.append(f"{prefix}: 'http_request' accepts either 'json' or 'body', not both")
    if has_json and not isinstance(value["json"], (dict, list)):
        errors.append(f"{prefix}: 'http_request' json must be a mapping or a list")
    if has_body and not isinstance(value["body"], str):
        errors.append(f"{prefix}: 'http_request' body must be a string")

    timeout = value.get("timeout")
    if timeout is not None and (
        not _is_number(timeout) or timeout <= 0 or timeout > HTTP_REQUEST_MAX_TIMEOUT
    ):
        errors.append(
            f"{prefix}: 'http_request' timeout must be a number greater than 0 "
            f"and at most {HTTP_REQUEST_MAX_TIMEOUT}"
        )

    retries = value.get("retries")
    if retries is not None and (
        not isinstance(retries, int)
        or isinstance(retries, bool)
        or not 0 <= retries <= HTTP_REQUEST_MAX_RETRIES
    ):
        errors.append(
            f"{prefix}: 'http_request' retries must be a whole number from 0 "
            f"to {HTTP_REQUEST_MAX_RETRIES}"
        )

    expect = value.get("expect_status")
    if expect is not None:
        codes = expect if isinstance(expect, list) else [expect]
        if not codes or not all(_is_status_code(c) for c in codes):
            errors.append(
                f"{prefix}: 'http_request' expect_status must be a status code "
                f"(100-599) or a non-empty list of them"
            )

    verify_tls = value.get("verify_tls")
    if verify_tls is not None and not isinstance(verify_tls, bool):
        errors.append(f"{prefix}: 'http_request' verify_tls must be true or false")

    return errors
```

- [ ] **Step 5: Call the helper from `_validate_step`**

In `_validate_step`, insert a branch immediately before the `elif step_type == "wait_webhook":` branch:

```python
    elif step_type == "http_request":
        errors.extend(_validate_http_request(value, prefix))

    elif step_type == "wait_webhook":
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_http_request_step.py -q`
Expected: `46 passed`.

Run the whole suite: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `348 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/pipeline_compiler.py backend/tests/test_http_request_step.py
git commit -m "feat(pipeline): validate and compile the http_request step"
```

---

### Task 2: Send the request from the agent

**Files:**
- Create: `agent/internal/executor/http_request.go`
- Create: `agent/internal/executor/http_request_test.go`
- Modify: `agent/internal/executor/local.go` (one branch in `Run`, after the `kube_apply` branch)

**Interfaces:**
- Consumes: the step config produced by Task 1, after the server has replaced placeholders. It arrives as `Step.Config map[string]interface{}` decoded from JSON, so numbers are `float64`, mappings are `map[string]interface{}` and lists are `[]interface{}`.
- Consumes (existing, same package): `configStr`, `configStrMap` in `local.go`; `protocol.StreamStdout`, `protocol.StreamStderr`, `protocol.StatusSuccess`, `protocol.StatusFailed`, `protocol.StatusCancelled`; the test helper `drainLogs` in `kube_apply_test.go`.
- Produces: `func (l *Local) runHTTPRequest(ctx context.Context, step Step, logs chan<- LogLine) Result`.
  - Success: `Result{ExitCode: 0, Status: "success"}`.
  - Failure: `Result{ExitCode: 1, Status: "failed", Err: err}` after writing `err` to stderr.
  - Cancelled context: `Result{ExitCode: -1, Status: "cancelled", Err: ctx.Err()}`.
- Produces: `parseHTTPRequestConfig(cfg map[string]interface{}) (*httpRequestSpec, error)` and the package variable `httpRequestBackoff func(retry int) time.Duration`, which tests replace to avoid real waits.

**Go facts this task relies on:**
- `http.Client` errors are `*url.Error` values whose text is `Post "<full url>": <cause>`. Only `urlErr.Err` may be logged.
- `json.Marshal` escapes `<`, `>` and `&`. An `Encoder` with `SetEscapeHTML(false)` does not, and it appends a newline that must be trimmed.
- Returning `http.ErrUseLastResponse` from `CheckRedirect` makes the client return the 3xx response instead of following it.
- A request built with `http.NewRequestWithContext` is aborted when its context is cancelled or times out.

- [ ] **Step 1: Write the failing tests**

Create `agent/internal/executor/http_request_test.go`:

```go
package executor

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// recordedRequest is what a test server saw.
type recordedRequest struct {
	Method string
	Path   string
	Header http.Header
	Body   string
}

// recordingServer answers every request with the given handler and records
// what it received.
func recordingServer(t *testing.T, handler func(w http.ResponseWriter, r *http.Request, n int)) (*httptest.Server, func() []recordedRequest) {
	t.Helper()
	var mu sync.Mutex
	var seen []recordedRequest
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		mu.Lock()
		seen = append(seen, recordedRequest{Method: r.Method, Path: r.URL.RequestURI(), Header: r.Header.Clone(), Body: string(body)})
		n := len(seen)
		mu.Unlock()
		handler(w, r, n)
	}))
	t.Cleanup(srv.Close)
	return srv, func() []recordedRequest {
		mu.Lock()
		defer mu.Unlock()
		return append([]recordedRequest(nil), seen...)
	}
}

func okHandler(w http.ResponseWriter, _ *http.Request, _ int) {
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write([]byte(`{"ok": true}`))
}

// fastBackoff removes the wait between retries for the duration of a test.
func fastBackoff(t *testing.T, d time.Duration) {
	t.Helper()
	original := httpRequestBackoff
	httpRequestBackoff = func(int) time.Duration { return d }
	t.Cleanup(func() { httpRequestBackoff = original })
}

// runHTTP runs an http_request step and returns its result and log output.
func runHTTP(t *testing.T, ctx context.Context, cfg map[string]interface{}) (Result, string) {
	t.Helper()
	l := NewLocal(Options{})
	logs := make(chan LogLine, 256)
	stop := drainLogs(logs)
	res := l.runHTTPRequest(ctx, Step{StepType: "http_request", Config: cfg}, logs)
	return res, stop()
}

func TestHTTPRequestSendsJSONBody(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url": srv.URL + "/hooks/abc",
		"json": map[string]interface{}{
			"text":  "Build <42> & done — ünïcode",
			"count": float64(3),
			"ok":    true,
			"tags":  []interface{}{"ci", "prod"},
		},
	})

	if res.Status != "success" || res.ExitCode != 0 {
		t.Fatalf("result = %+v, logs:\n%s", res, out)
	}
	reqs := seen()
	if len(reqs) != 1 {
		t.Fatalf("server saw %d requests, want 1", len(reqs))
	}
	if reqs[0].Method != "POST" {
		t.Errorf("method = %q, want POST (the default)", reqs[0].Method)
	}
	if got := reqs[0].Header.Get("Content-Type"); got != "application/json" {
		t.Errorf("Content-Type = %q, want application/json", got)
	}
	var decoded map[string]interface{}
	if err := json.Unmarshal([]byte(reqs[0].Body), &decoded); err != nil {
		t.Fatalf("body is not JSON: %v (%q)", err, reqs[0].Body)
	}
	if decoded["text"] != "Build <42> & done — ünïcode" || decoded["count"] != float64(3) || decoded["ok"] != true {
		t.Errorf("decoded body = %#v", decoded)
	}
	if !strings.Contains(reqs[0].Body, "<42> &") {
		t.Errorf("body should carry <, > and & as written, got %q", reqs[0].Body)
	}
}

func TestHTTPRequestJSONListBody(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{
		"url":  srv.URL,
		"json": []interface{}{"a", float64(1)},
	})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if got := seen()[0].Body; got != `["a",1]` {
		t.Errorf("body = %q", got)
	}
}

func TestHTTPRequestKeepsExplicitContentTypeWithJSON(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"headers": map[string]interface{}{"content-type": "application/vnd.api+json"},
		"json":    map[string]interface{}{"a": "b"},
	})

	if got := seen()[0].Header.Values("Content-Type"); len(got) != 1 || got[0] != "application/vnd.api+json" {
		t.Errorf("Content-Type = %v, want only the explicit one", got)
	}
}

func TestHTTPRequestSendsRawBodyUnchanged(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)
	raw := "<deploy>\n  <build>42</build>\n</deploy>\n"

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"method":  "put",
		"headers": map[string]interface{}{"Content-Type": "application/xml", "X-Attempt": float64(2), "X-Dry": true},
		"body":    raw,
	})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	req := seen()[0]
	if req.Method != "PUT" {
		t.Errorf("method = %q, want PUT", req.Method)
	}
	if req.Body != raw {
		t.Errorf("body = %q, want %q", req.Body, raw)
	}
	if req.Header.Get("Content-Type") != "application/xml" {
		t.Errorf("Content-Type = %q", req.Header.Get("Content-Type"))
	}
	if req.Header.Get("X-Attempt") != "2" || req.Header.Get("X-Dry") != "true" {
		t.Errorf("non-string header values not sent as strings: %v", req.Header)
	}
}

func TestHTTPRequestRawBodyGetsNoContentType(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "body": "plain"})

	if got := seen()[0].Header.Get("Content-Type"); got != "" {
		t.Errorf("Content-Type = %q, want none", got)
	}
}

func TestHTTPRequestWithoutBody(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "method": "GET"})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if req := seen()[0]; req.Method != "GET" || req.Body != "" {
		t.Errorf("request = %+v", req)
	}
}

func TestHTTPRequestNon2xxFailsWithoutRetry(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		http.Error(w, "no such hook", http.StatusNotFound)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "retries": float64(3)})

	if res.Status != "failed" || res.ExitCode != 1 {
		t.Fatalf("result = %+v", res)
	}
	if len(seen()) != 1 {
		t.Errorf("a 404 must not be retried; server saw %d requests", len(seen()))
	}
	if !strings.Contains(out, "unexpected status 404 (expected 2xx)") {
		t.Errorf("logs should state the unexpected status, got:\n%s", out)
	}
	if !strings.Contains(out, "no such hook") {
		t.Errorf("logs should show the response body, got:\n%s", out)
	}
}

func TestHTTPRequestExpectStatusAcceptsListedNon2xx(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusConflict)
	})

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{
		"url":           srv.URL,
		"expect_status": []interface{}{float64(200), float64(409)},
	})

	if res.Status != "success" {
		t.Errorf("status = %q, want success for a listed 409", res.Status)
	}
}

func TestHTTPRequestExpectStatusRejectsUnlisted2xx(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusOK)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url":           srv.URL,
		"expect_status": float64(202),
	})

	if res.Status != "failed" {
		t.Errorf("status = %q, want failed", res.Status)
	}
	if !strings.Contains(out, "unexpected status 200 (expected 202)") {
		t.Errorf("logs = %s", out)
	}
}

func TestHTTPRequestExpectedServerErrorIsSuccessNotRetried(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusServiceUnavailable)
	})

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{
		"url":           srv.URL,
		"retries":       float64(2),
		"expect_status": float64(503),
	})

	if res.Status != "success" || len(seen()) != 1 {
		t.Errorf("status = %q after %d requests, want success after 1", res.Status, len(seen()))
	}
}

func TestHTTPRequestRetriesServerErrorThenSucceeds(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, n int) {
		if n == 1 {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"retries": float64(1),
		"json":    map[string]interface{}{"n": float64(1)},
	})

	if res.Status != "success" {
		t.Fatalf("status = %q, logs:\n%s", res.Status, out)
	}
	reqs := seen()
	if len(reqs) != 2 {
		t.Fatalf("server saw %d requests, want 2", len(reqs))
	}
	if reqs[0].Body != reqs[1].Body || reqs[1].Body != `{"n":1}` {
		t.Errorf("the body must be re-sent in full on retry: %q then %q", reqs[0].Body, reqs[1].Body)
	}
	if !strings.Contains(out, "attempt 1 of 2") || !strings.Contains(out, "attempt 2 of 2") || !strings.Contains(out, "retrying in") {
		t.Errorf("logs should show both attempts and the retry, got:\n%s", out)
	}
}

func TestHTTPRequestServerErrorWithoutRetriesFails(t *testing.T) {
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusBadGateway)
	})

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "failed" || len(seen()) != 1 {
		t.Errorf("status = %q after %d requests, want failed after 1", res.Status, len(seen()))
	}
}

func TestHTTPRequestRetriesTooManyRequests(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, n int) {
		if n < 3 {
			w.WriteHeader(http.StatusTooManyRequests)
			return
		}
		w.WriteHeader(http.StatusNoContent)
	})

	res, _ := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "retries": float64(2)})

	if res.Status != "success" || len(seen()) != 3 {
		t.Errorf("status = %q after %d requests, want success after 3", res.Status, len(seen()))
	}
}

func TestHTTPRequestGivesUpAfterAllRetries(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv, seen := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusInternalServerError)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "retries": float64(2)})

	if res.Status != "failed" || len(seen()) != 3 {
		t.Errorf("status = %q after %d requests, want failed after 3", res.Status, len(seen()))
	}
	if !strings.Contains(out, "unexpected status 500") {
		t.Errorf("logs = %s", out)
	}
}

func TestHTTPRequestTimesOut(t *testing.T) {
	release := make(chan struct{})
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		select {
		case <-release:
		case <-r.Context().Done():
		}
	})
	defer close(release)

	started := time.Now()
	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "timeout": 0.2})

	if res.Status != "failed" {
		t.Fatalf("status = %q, want failed", res.Status)
	}
	if elapsed := time.Since(started); elapsed > 5*time.Second {
		t.Errorf("timeout was not applied: took %s", elapsed)
	}
	if !strings.Contains(out, "timed out after 200ms") {
		t.Errorf("logs should name the timeout, got:\n%s", out)
	}
}

func TestHTTPRequestConnectionRefusedIsRetried(t *testing.T) {
	fastBackoff(t, time.Millisecond)
	srv := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {}))
	deadURL := srv.URL + "/secret-path-token"
	srv.Close()

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": deadURL, "retries": float64(1)})

	if res.Status != "failed" {
		t.Fatalf("status = %q, want failed", res.Status)
	}
	if !strings.Contains(out, "attempt 2 of 2") {
		t.Errorf("a network error should be retried, got:\n%s", out)
	}
	if !strings.Contains(out, "request failed") {
		t.Errorf("logs = %s", out)
	}
	if strings.Contains(out, "secret-path-token") {
		t.Errorf("a network error must not reveal the URL path:\n%s", out)
	}
}

func TestHTTPRequestCancelledBeforeSending(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	res, _ := runHTTP(t, ctx, map[string]interface{}{"url": srv.URL})

	if res.Status != "cancelled" {
		t.Errorf("status = %q, want cancelled", res.Status)
	}
	if len(seen()) != 0 {
		t.Errorf("nothing should be sent after cancellation; server saw %d requests", len(seen()))
	}
}

func TestHTTPRequestCancelledWhileWaitingForResponse(t *testing.T) {
	arrived := make(chan struct{})
	var once sync.Once
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		once.Do(func() { close(arrived) })
		<-r.Context().Done()
	})
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		<-arrived
		cancel()
	}()

	started := time.Now()
	res, _ := runHTTP(t, ctx, map[string]interface{}{"url": srv.URL, "timeout": float64(60)})

	if res.Status != "cancelled" {
		t.Errorf("status = %q, want cancelled", res.Status)
	}
	if elapsed := time.Since(started); elapsed > 10*time.Second {
		t.Errorf("cancellation did not abort the request: took %s", elapsed)
	}
}

func TestHTTPRequestCancelledDuringRetryWait(t *testing.T) {
	fastBackoff(t, time.Minute)
	var hits int32
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		atomic.AddInt32(&hits, 1)
		w.WriteHeader(http.StatusInternalServerError)
	})
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		for atomic.LoadInt32(&hits) == 0 {
			time.Sleep(5 * time.Millisecond)
		}
		time.Sleep(50 * time.Millisecond)
		cancel()
	}()

	started := time.Now()
	res, _ := runHTTP(t, ctx, map[string]interface{}{"url": srv.URL, "retries": float64(3)})

	if res.Status != "cancelled" {
		t.Errorf("status = %q, want cancelled", res.Status)
	}
	if elapsed := time.Since(started); elapsed > 10*time.Second {
		t.Errorf("cancellation did not interrupt the retry wait: took %s", elapsed)
	}
}

func TestHTTPRequestDoesNotFollowRedirects(t *testing.T) {
	var targetHits int32
	target := httptest.NewServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		atomic.AddInt32(&targetHits, 1)
	}))
	defer target.Close()
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		http.Redirect(w, r, target.URL, http.StatusFound)
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url":     srv.URL,
		"headers": map[string]interface{}{"Authorization": "Bearer redirect-test-token"},
	})

	if res.Status != "failed" {
		t.Errorf("status = %q, want failed", res.Status)
	}
	if atomic.LoadInt32(&targetHits) != 0 {
		t.Errorf("the redirect target was contacted %d times", targetHits)
	}
	if !strings.Contains(out, "unexpected status 302 (expected 2xx; redirects are not followed)") {
		t.Errorf("logs = %s", out)
	}
}

func TestHTTPRequestSelfSignedCertificate(t *testing.T) {
	srv := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})
	if res.Status != "failed" {
		t.Errorf("default: status = %q, want failed for a self-signed certificate", res.Status)
	}
	if !strings.Contains(out, "verify_tls: false") {
		t.Errorf("the failure should point to verify_tls, got:\n%s", out)
	}

	res, out = runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL, "verify_tls": false})
	if res.Status != "success" {
		t.Errorf("verify_tls false: status = %q, logs:\n%s", res.Status, out)
	}
	if !strings.Contains(out, "certificate verification is disabled") {
		t.Errorf("disabling verification should be logged, got:\n%s", out)
	}
}

func TestHTTPRequestLogsNeverContainSecrets(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, r *http.Request, _ int) {
		// A receiver that echoes what it was sent.
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("auth=" + r.Header.Get("Authorization") + " token=tok-9f8e7d6c5b4a key=" + r.Header.Get("X-Api-Key")))
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{
		"url": srv.URL + "/services/T000/B000/path-secret-xyz?sig=query-secret-abc",
		"headers": map[string]interface{}{
			"Authorization": "Bearer tok-9f8e7d6c5b4a",
			"X-Api-Key":     "key-1122334455",
		},
		"json": map[string]interface{}{"password": "body-secret-777"},
	})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	for _, secret := range []string{"path-secret-xyz", "query-secret-abc", "tok-9f8e7d6c5b4a", "key-1122334455", "body-secret-777"} {
		if strings.Contains(out, secret) {
			t.Errorf("logs contain %q:\n%s", secret, out)
		}
	}
	host := strings.TrimPrefix(srv.URL, "http://")
	if !strings.Contains(out, "POST http://"+host+"/… (attempt 1 of 1)") {
		t.Errorf("logs should show the method, scheme and host only, got:\n%s", out)
	}
	if !strings.Contains(out, "200 OK in ") || !strings.Contains(out, "auth=*** token=*** key=***") {
		t.Errorf("logs should show the status and the masked response, got:\n%s", out)
	}
}

func TestHTTPRequestURLUserInfoIsNotLogged(t *testing.T) {
	srv, _ := recordingServer(t, okHandler)
	withUser := strings.Replace(srv.URL, "http://", "http://deploy:userinfo-secret@", 1)

	_, out := runHTTP(t, context.Background(), map[string]interface{}{"url": withUser})

	if strings.Contains(out, "userinfo-secret") || strings.Contains(out, "deploy:") {
		t.Errorf("logs contain URL credentials:\n%s", out)
	}
}

func TestHTTPRequestLongResponseIsTruncatedInLogs(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		_, _ = w.Write([]byte(strings.Repeat("x", 5000) + "THE-END"))
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if !strings.Contains(out, "Response (first 2000 characters):") {
		t.Errorf("logs should say the response was truncated, got %d bytes of logs", len(out))
	}
	if strings.Contains(out, "THE-END") || strings.Count(out, "x") > 2100 {
		t.Errorf("response was not truncated: %d x characters in the logs", strings.Count(out, "x"))
	}
}

func TestHTTPRequestHugeResponseDoesNotStallOrFlood(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		chunk := []byte(strings.Repeat("y", 64*1024))
		for i := 0; i < 80; i++ { // ~5 MiB
			if _, err := w.Write(chunk); err != nil {
				return
			}
		}
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if len(out) > 8*1024 {
		t.Errorf("logs are %d bytes for a 5 MiB response", len(out))
	}
}

func TestHTTPRequestEmptyResponsePrintsNoResponseSection(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		w.WriteHeader(http.StatusNoContent)
	})

	_, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if strings.Contains(out, "Response") {
		t.Errorf("an empty response should print no response section, got:\n%s", out)
	}
	if !strings.Contains(out, "204 No Content in ") {
		t.Errorf("logs = %s", out)
	}
}

func TestHTTPRequestBinaryResponseIsLoggedAsValidText(t *testing.T) {
	srv, _ := recordingServer(t, func(w http.ResponseWriter, _ *http.Request, _ int) {
		_, _ = w.Write([]byte{0xff, 0xfe, 'o', 'k', 0x80})
	})

	res, out := runHTTP(t, context.Background(), map[string]interface{}{"url": srv.URL})

	if res.Status != "success" {
		t.Fatalf("status = %q", res.Status)
	}
	if strings.ToValidUTF8(out, "") != out {
		t.Errorf("logs contain invalid UTF-8: %q", out)
	}
}

func TestHTTPRequestRejectsBadConfigBeforeSending(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)

	cases := []struct {
		name string
		cfg  map[string]interface{}
		want string
	}{
		{"missing url", map[string]interface{}{}, "missing 'url'"},
		{"empty url (unresolved secret)", map[string]interface{}{"url": "  "}, "missing 'url'"},
		{"no scheme", map[string]interface{}{"url": "hooks.example.com/x"}, "absolute http:// or https://"},
		{"other scheme", map[string]interface{}{"url": "ftp://example.com/x"}, "absolute http:// or https://"},
		{"no host", map[string]interface{}{"url": "https:///path-only"}, "absolute http:// or https://"},
		{"bad method", map[string]interface{}{"url": srv.URL, "method": "TRACE"}, "'method' must be one of"},
		{"headers not a mapping", map[string]interface{}{"url": srv.URL, "headers": "X: y"}, "'headers' must be a mapping"},
		{"json and body", map[string]interface{}{"url": srv.URL, "json": map[string]interface{}{}, "body": "x"}, "either 'json' or 'body'"},
		{"json scalar", map[string]interface{}{"url": srv.URL, "json": "text"}, "'json' must be a mapping or a list"},
		{"body not a string", map[string]interface{}{"url": srv.URL, "body": float64(5)}, "'body' must be a string"},
		{"timeout zero", map[string]interface{}{"url": srv.URL, "timeout": float64(0)}, "'timeout' must be"},
		{"timeout too large", map[string]interface{}{"url": srv.URL, "timeout": float64(301)}, "'timeout' must be"},
		{"timeout not a number", map[string]interface{}{"url": srv.URL, "timeout": "30"}, "'timeout' must be"},
		{"retries negative", map[string]interface{}{"url": srv.URL, "retries": float64(-1)}, "'retries' must be"},
		{"retries too many", map[string]interface{}{"url": srv.URL, "retries": float64(6)}, "'retries' must be"},
		{"retries fractional", map[string]interface{}{"url": srv.URL, "retries": 1.5}, "'retries' must be"},
		{"expect_status out of range", map[string]interface{}{"url": srv.URL, "expect_status": float64(99)}, "'expect_status' must be"},
		{"expect_status empty list", map[string]interface{}{"url": srv.URL, "expect_status": []interface{}{}}, "'expect_status' must be"},
		{"expect_status string", map[string]interface{}{"url": srv.URL, "expect_status": "200"}, "'expect_status' must be"},
		{"verify_tls not a boolean", map[string]interface{}{"url": srv.URL, "verify_tls": "no"}, "'verify_tls' must be"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			res, out := runHTTP(t, context.Background(), tc.cfg)
			if res.Status != "failed" || res.ExitCode != 1 {
				t.Errorf("result = %+v, want failed", res)
			}
			if !strings.Contains(out, tc.want) {
				t.Errorf("logs should contain %q, got:\n%s", tc.want, out)
			}
		})
	}
	if n := len(seen()); n != 0 {
		t.Errorf("a rejected config must send nothing; server saw %d requests", n)
	}
}

func TestHTTPRequestBadURLErrorDoesNotEchoTheURL(t *testing.T) {
	_, out := runHTTP(t, context.Background(), map[string]interface{}{"url": "ftp://user:url-secret-123@example.com/x"})

	if strings.Contains(out, "url-secret-123") {
		t.Errorf("the rejected URL was written to the logs:\n%s", out)
	}
}

func TestHTTPRequestBackoffDoubles(t *testing.T) {
	want := []time.Duration{time.Second, 2 * time.Second, 4 * time.Second, 8 * time.Second, 16 * time.Second}
	for i, w := range want {
		if got := httpRequestBackoff(i + 1); got != w {
			t.Errorf("backoff before retry %d = %s, want %s", i+1, got, w)
		}
	}
}

func TestLocalRunDispatchesHTTPRequest(t *testing.T) {
	srv, seen := recordingServer(t, okHandler)
	l := NewLocal(Options{Workdir: t.TempDir()})
	logs := make(chan LogLine, 64)
	var collected strings.Builder
	done := make(chan struct{})
	go func() {
		defer close(done)
		for line := range logs {
			collected.WriteString(line.Content)
		}
	}()

	res := l.Run(context.Background(), Step{
		BuildID:  "b1",
		StepID:   "s1",
		StepType: "http_request",
		Config:   map[string]interface{}{"url": srv.URL, "json": map[string]interface{}{"a": "b"}},
	}, logs)
	<-done

	if res.Status != "success" {
		t.Fatalf("status = %q, logs:\n%s", res.Status, collected.String())
	}
	if len(seen()) != 1 {
		t.Errorf("server saw %d requests, want 1", len(seen()))
	}
}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `agent/`): `go test ./internal/executor/ -count=1`
Expected: FAIL at compile time, with errors such as `undefined: httpRequestBackoff` and `l.runHTTPRequest undefined`.

- [ ] **Step 3: Implement the step**

Create `agent/internal/executor/http_request.go`:

```go
package executor

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/megooci/megooci-agent/internal/protocol"
)

const (
	defaultHTTPRequestTimeoutSec = 30
	maxHTTPRequestTimeoutSec     = 300
	maxHTTPRequestRetries        = 5
	// httpResponseReadLimit bounds how much of a response body is read at all.
	httpResponseReadLimit = 64 * 1024
	// httpResponseLogChars bounds how much of it is written to the build log.
	httpResponseLogChars = 2000
	// Values shorter than this are not masked: replacing them would mangle
	// ordinary output without hiding anything meaningful.
	httpMaskMinLen = 4
	// A space-separated part of a header value ("Bearer <token>") is masked
	// on its own when it is at least this long.
	httpMaskMinPartLen = 8
)

var httpRequestMethods = []string{"GET", "POST", "PUT", "PATCH", "DELETE"}

// httpRequestBackoff is the wait before retry n (1-based): 1s, 2s, 4s, ...
// A variable so tests can shorten it.
var httpRequestBackoff = func(retry int) time.Duration {
	return time.Duration(1<<uint(retry-1)) * time.Second
}

// httpRequestSpec is a validated http_request step configuration.
type httpRequestSpec struct {
	Method    string
	URL       *url.URL
	RawURL    string
	Headers   map[string]string
	Body      []byte
	Timeout   time.Duration
	Retries   int
	Expect    []int // empty means "any 2xx"
	VerifyTLS bool
}

// configNumber reads a numeric config value. JSON numbers arrive as float64
// after the controller round-trip; ints appear in tests and local callers.
func configNumber(cfg map[string]interface{}, key string) (value float64, present bool, ok bool) {
	raw, exists := cfg[key]
	if !exists || raw == nil {
		return 0, false, true
	}
	switch v := raw.(type) {
	case float64:
		return v, true, true
	case int:
		return float64(v), true, true
	case int64:
		return float64(v), true, true
	}
	return 0, true, false
}

func isWholeNumber(v float64) bool { return v == float64(int64(v)) }

func isHTTPStatus(v float64) bool { return isWholeNumber(v) && v >= 100 && v <= 599 }

// parseHTTPRequestConfig validates the step config and builds the request
// description. Error messages never include the URL, a header value or the
// body: any of them may be a secret.
func parseHTTPRequestConfig(cfg map[string]interface{}) (*httpRequestSpec, error) {
	spec := &httpRequestSpec{
		Method:    "POST",
		Timeout:   defaultHTTPRequestTimeoutSec * time.Second,
		VerifyTLS: true,
	}

	rawURL := strings.TrimSpace(configStr(cfg, "url"))
	if rawURL == "" {
		return nil, errors.New("http_request: missing 'url'. If it comes from a secret, verify the secret exists and is in scope for this pipeline")
	}
	parsed, err := url.Parse(rawURL)
	if err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		// Deliberately not wrapping err: url.Parse errors quote the URL.
		return nil, errors.New("http_request: 'url' must be an absolute http:// or https:// URL")
	}
	spec.URL = parsed
	spec.RawURL = rawURL

	if raw, exists := cfg["method"]; exists && raw != nil {
		method := strings.ToUpper(strings.TrimSpace(configStr(cfg, "method")))
		allowed := false
		for _, m := range httpRequestMethods {
			if m == method {
				allowed = true
			}
		}
		if !allowed {
			return nil, fmt.Errorf("http_request: 'method' must be one of: %s", strings.Join(httpRequestMethods, ", "))
		}
		spec.Method = method
	}

	if raw, exists := cfg["headers"]; exists && raw != nil {
		if _, isMap := raw.(map[string]interface{}); !isMap {
			return nil, errors.New("http_request: 'headers' must be a mapping")
		}
		spec.Headers = configStrMap(cfg, "headers")
	}

	jsonValue, hasJSON := cfg["json"]
	bodyValue, hasBody := cfg["body"]
	if hasJSON && hasBody {
		return nil, errors.New("http_request: use either 'json' or 'body', not both")
	}
	if hasJSON {
		switch jsonValue.(type) {
		case map[string]interface{}, []interface{}:
		default:
			return nil, errors.New("http_request: 'json' must be a mapping or a list")
		}
		// An Encoder rather than json.Marshal so that <, > and & are sent
		// as written instead of as <-style escapes.
		var encoded bytes.Buffer
		encoder := json.NewEncoder(&encoded)
		encoder.SetEscapeHTML(false)
		if err := encoder.Encode(jsonValue); err != nil {
			return nil, errors.New("http_request: 'json' could not be encoded")
		}
		spec.Body = bytes.TrimRight(encoded.Bytes(), "\n")
		if !hasHeader(spec.Headers, "Content-Type") {
			if spec.Headers == nil {
				spec.Headers = map[string]string{}
			}
			spec.Headers["Content-Type"] = "application/json"
		}
	}
	if hasBody {
		text, isString := bodyValue.(string)
		if !isString {
			return nil, errors.New("http_request: 'body' must be a string")
		}
		spec.Body = []byte(text)
	}

	if v, present, ok := configNumber(cfg, "timeout"); present {
		if !ok || v <= 0 || v > maxHTTPRequestTimeoutSec {
			return nil, fmt.Errorf("http_request: 'timeout' must be a number of seconds greater than 0 and at most %d", maxHTTPRequestTimeoutSec)
		}
		spec.Timeout = time.Duration(v * float64(time.Second))
	}

	if v, present, ok := configNumber(cfg, "retries"); present {
		if !ok || !isWholeNumber(v) || v < 0 || v > maxHTTPRequestRetries {
			return nil, fmt.Errorf("http_request: 'retries' must be a whole number from 0 to %d", maxHTTPRequestRetries)
		}
		spec.Retries = int(v)
	}

	if raw, exists := cfg["expect_status"]; exists && raw != nil {
		badExpect := errors.New("http_request: 'expect_status' must be a status code (100-599) or a non-empty list of them")
		if list, isList := raw.([]interface{}); isList {
			if len(list) == 0 {
				return nil, badExpect
			}
			for _, item := range list {
				v, _, ok := configNumber(map[string]interface{}{"v": item}, "v")
				if !ok || !isHTTPStatus(v) {
					return nil, badExpect
				}
				spec.Expect = append(spec.Expect, int(v))
			}
		} else {
			v, _, ok := configNumber(cfg, "expect_status")
			if !ok || !isHTTPStatus(v) {
				return nil, badExpect
			}
			spec.Expect = []int{int(v)}
		}
	}

	if raw, exists := cfg["verify_tls"]; exists && raw != nil {
		verify, isBool := raw.(bool)
		if !isBool {
			return nil, errors.New("http_request: 'verify_tls' must be true or false")
		}
		spec.VerifyTLS = verify
	}

	return spec, nil
}

func hasHeader(headers map[string]string, name string) bool {
	for key := range headers {
		if strings.EqualFold(key, name) {
			return true
		}
	}
	return false
}

// displayURL is the only form of the URL written to the build log: scheme
// and host. The path, query and user-info often carry the secret.
func (s *httpRequestSpec) displayURL() string {
	return s.URL.Scheme + "://" + s.URL.Host + "/…"
}

func (s *httpRequestSpec) statusExpected(status int) bool {
	if len(s.Expect) == 0 {
		return status >= 200 && status <= 299
	}
	for _, want := range s.Expect {
		if want == status {
			return true
		}
	}
	return false
}

func (s *httpRequestSpec) expectedText() string {
	if len(s.Expect) == 0 {
		return "2xx"
	}
	parts := make([]string, len(s.Expect))
	for i, code := range s.Expect {
		parts[i] = fmt.Sprintf("%d", code)
	}
	return strings.Join(parts, ", ")
}

// mask replaces the request's URL and header values wherever they appear in
// text, in case the receiver (or an error message) echoes them back.
func (s *httpRequestSpec) mask(text string) string {
	secrets := []string{s.RawURL}
	for _, value := range s.Headers {
		secrets = append(secrets, value)
		if strings.Contains(value, " ") {
			for _, part := range strings.Fields(value) {
				if len(part) >= httpMaskMinPartLen {
					secrets = append(secrets, part)
				}
			}
		}
	}
	for _, secret := range secrets {
		if len(secret) >= httpMaskMinLen {
			text = strings.ReplaceAll(text, secret, "***")
		}
	}
	return text
}

func newHTTPRequestClient(spec *httpRequestSpec) (*http.Client, *http.Transport) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if !spec.VerifyTLS {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec // opt-in via verify_tls: false
	}
	client := &http.Client{
		Transport: transport,
		// Never follow redirects: that could resend credentials to another
		// address, and a redirected POST often becomes a GET.
		CheckRedirect: func(*http.Request, []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	return client, transport
}

type httpAttemptResult struct {
	Status     int
	StatusText string
	Body       []byte
	Elapsed    time.Duration
}

// sendHTTPRequest performs one attempt, bounded by the step timeout.
func sendHTTPRequest(ctx context.Context, client *http.Client, spec *httpRequestSpec) (*httpAttemptResult, error) {
	attemptCtx, cancel := context.WithTimeout(ctx, spec.Timeout)
	defer cancel()

	var body io.Reader
	if spec.Body != nil {
		body = bytes.NewReader(spec.Body)
	}
	req, err := http.NewRequestWithContext(attemptCtx, spec.Method, spec.URL.String(), body)
	if err != nil {
		return nil, errors.New("could not build the request")
	}
	for name, value := range spec.Headers {
		req.Header.Set(name, value)
	}

	started := time.Now()
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(io.LimitReader(resp.Body, httpResponseReadLimit))
	return &httpAttemptResult{
		Status:     resp.StatusCode,
		StatusText: resp.Status,
		Body:       data,
		Elapsed:    time.Since(started),
	}, nil
}

// describeHTTPError renders a transport error without the URL. The standard
// client wraps errors as `Post "<full url>": <cause>`; only the cause is kept.
func describeHTTPError(err error, spec *httpRequestSpec) string {
	var urlErr *url.Error
	if errors.As(err, &urlErr) && urlErr.Err != nil {
		err = urlErr.Err
	}
	message := spec.mask(err.Error())
	if errors.Is(err, context.DeadlineExceeded) {
		return fmt.Sprintf("timed out after %s", spec.Timeout)
	}
	if strings.Contains(message, "x509:") {
		message += " (set 'verify_tls: false' to accept a self-signed certificate)"
	}
	return message
}

// runHTTPRequest handles http_request steps natively: one HTTP request sent
// from the agent, with optional retries. The URL path, request headers and
// request body are never written to the build log.
func (l *Local) runHTTPRequest(ctx context.Context, step Step, logs chan<- LogLine) Result {
	emit := func(stream, text string) {
		select {
		case logs <- LogLine{Stream: stream, Content: text}:
		case <-ctx.Done():
		}
	}
	fail := func(err error) Result {
		emit(protocol.StreamStderr, err.Error()+"\n")
		return Result{ExitCode: 1, Status: protocol.StatusFailed, Err: err}
	}
	cancelled := func() Result {
		return Result{ExitCode: -1, Status: protocol.StatusCancelled, Err: ctx.Err()}
	}

	spec, err := parseHTTPRequestConfig(step.Config)
	if err != nil {
		return fail(err)
	}
	if ctx.Err() != nil {
		return cancelled()
	}

	client, transport := newHTTPRequestClient(spec)
	defer transport.CloseIdleConnections()

	if !spec.VerifyTLS {
		emit(protocol.StreamStderr, "Warning: TLS certificate verification is disabled for this request (verify_tls: false)\n")
	}

	attempts := spec.Retries + 1
	var lastErr error
	for attempt := 1; attempt <= attempts; attempt++ {
		emit(protocol.StreamStdout, fmt.Sprintf("%s %s (attempt %d of %d)\n", spec.Method, spec.displayURL(), attempt, attempts))

		result, err := sendHTTPRequest(ctx, client, spec)
		if ctx.Err() != nil {
			return cancelled()
		}

		retryable := false
		if err != nil {
			lastErr = fmt.Errorf("http_request: request failed: %s", describeHTTPError(err, spec))
			retryable = true
		} else {
			emit(protocol.StreamStdout, fmt.Sprintf("%s in %d ms\n", result.StatusText, result.Elapsed.Milliseconds()))
			logHTTPResponseBody(result.Body, spec, emit)
			if spec.statusExpected(result.Status) {
				return Result{ExitCode: 0, Status: protocol.StatusSuccess}
			}
			note := ""
			if result.Status >= 300 && result.Status <= 399 {
				note = "; redirects are not followed"
			}
			lastErr = fmt.Errorf("http_request: unexpected status %d (expected %s%s)", result.Status, spec.expectedText(), note)
			retryable = result.Status >= 500 || result.Status == http.StatusTooManyRequests
		}

		if !retryable || attempt == attempts {
			break
		}
		wait := httpRequestBackoff(attempt)
		emit(protocol.StreamStderr, fmt.Sprintf("%s; retrying in %s\n", lastErr, wait))
		select {
		case <-time.After(wait):
		case <-ctx.Done():
			return cancelled()
		}
	}
	return fail(lastErr)
}

// logHTTPResponseBody writes the start of the response body to the build log,
// with the request's own URL and header values masked.
func logHTTPResponseBody(body []byte, spec *httpRequestSpec, emit func(stream, text string)) {
	text := strings.TrimRight(strings.ToValidUTF8(string(body), "�"), "\r\n")
	if strings.TrimSpace(text) == "" {
		return
	}
	text = spec.mask(text)
	runes := []rune(text)
	if len(runes) > httpResponseLogChars {
		text = string(runes[:httpResponseLogChars])
		emit(protocol.StreamStdout, fmt.Sprintf("Response (first %d characters):\n", httpResponseLogChars))
	} else {
		emit(protocol.StreamStdout, "Response:\n")
	}
	for _, line := range strings.Split(text, "\n") {
		emit(protocol.StreamStdout, strings.TrimRight(line, "\r")+"\n")
	}
}
```

- [ ] **Step 4: Dispatch the step type**

In `agent/internal/executor/local.go`, inside `func (l *Local) Run(`, add a branch after the `kube_apply` branch:

```go
	if step.StepType == "kube_apply" {
		return l.runKubeApply(ctx, step, workdir, logs)
	}
	if step.StepType == "http_request" {
		return l.runHTTPRequest(ctx, step, logs)
	}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run (from `agent/`):

```bash
go test ./internal/executor/ -count=1
go vet ./...
gofmt -l internal/executor/http_request.go internal/executor/http_request_test.go
```

Expected: `ok  github.com/megooci/megooci-agent/internal/executor`, no output from `go vet`, and no output from `gofmt` for those two files.

Run the tests three more times to rule out timing flakiness: `go test ./internal/executor/ -count=3`
Expected: `ok`.

- [ ] **Step 6: Commit**

```bash
git add agent/internal/executor/http_request.go agent/internal/executor/http_request_test.go agent/internal/executor/local.go
git commit -m "feat(agent): send http_request steps natively"
```

---

### Task 3: Teach the AI assistant the step

**Files:**
- Modify: `backend/app/api/v1/ai_assistant.py` (`SYSTEM_PROMPT`: a new section, the Placeholders list, rule 7)
- Test: `backend/tests/test_http_request_docs.py` (new)

**Interfaces:**
- Consumes: `HTTP_REQUEST_FIELDS` and `validate_pipeline` from `app.services.pipeline_compiler` (Task 1).
- Produces: `SYSTEM_PROMPT` contains a `### http_request` section between `### kube_apply` and `### wait_webhook`, whose YAML examples all pass `validate_pipeline`.

`SYSTEM_PROMPT` is an ordinary triple-quoted string. A backslash at the end of a prose line joins it to the next line, which is how the existing prompt wraps long sentences. Lines inside the YAML code blocks must not end with a backslash.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_http_request_docs.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_http_request_docs.py -q`
Expected: `6 failed`, with `ValueError: substring not found` for the missing section and assertion errors for the placeholder list and rule 7.

- [ ] **Step 3: Add the prompt section**

In `backend/app/api/v1/ai_assistant.py`, inside `SYSTEM_PROMPT`, insert the following text immediately above the line `### wait_webhook — Pause until an external webhook callback`. Insert it exactly, including the trailing blank line:

````text
### http_request — Send an HTTP request to an external system
```yaml
- http_request:
    url: ${{ secrets.DEPLOY_WEBHOOK_URL }}   # required; http:// or https://
    method: POST                             # optional: GET, POST, PUT, PATCH, DELETE (default POST)
    headers:                                 # optional
      Authorization: Bearer ${{ secrets.DEPLOY_API_TOKEN }}
    json:                                    # optional; any mapping or list, sent as JSON
      text: "Build #${{ build.number }} of ${{ pipeline.name }} deployed"
      branch: ${{ build.branch }}
      tags: [ci, production]
    timeout: 30                              # optional seconds per attempt (default 30, max 300)
    retries: 2                               # optional extra attempts (default 0, max 5)
    expect_status: [200, 202]                # optional; one code or a list (default: any 2xx)
    verify_tls: true                         # optional; false accepts a self-signed certificate
```

For a payload that is not JSON, use `body` instead of `json` and set the content \
type yourself:
```yaml
- http_request:
    url: https://legacy.example.com/hook
    headers:
      Content-Type: application/xml
    body: |
      <deploy><build>${{ build.number }}</build></deploy>
```

Use `http_request` to call any external URL: a chat webhook (Teams, Discord), a \
deployment or ticketing system, or the user's own service. The payload has no \
fixed shape — write whatever the receiving system expects under `json`. \
`json` and `body` cannot be combined, and both are optional. Only these fields \
exist: `url`, `method`, `headers`, `json`, `body`, `timeout`, `retries`, \
`expect_status`, `verify_tls`; any other field is rejected.

Placeholders always produce strings, so `count: ${{ build.number }}` sends \
`"42"`, while literal YAML numbers and booleans keep their type. When a number \
or boolean must come from a placeholder, use `body` with hand-written JSON and a \
`Content-Type: application/json` header.

The step fails the build when the response status is not the expected one. \
Retries apply to network errors, 5xx and 429 only. Redirects are not followed, \
and the response cannot be used by later steps. The request is sent from the \
build agent. Most webhook URLs contain a token, so take the URL from \
`${{ secrets.X }}`, and take every credential from a secret too. To send through \
a channel configured in Notification Channels, use `notify` instead.

````

- [ ] **Step 4: List the build placeholders**

In the `## Placeholders` part of `SYSTEM_PROMPT`, add a third bullet after the `${{ env.NAME }}` bullet:

```text
- `${{ env.NAME }}` — replaced at runtime with environment variables
- `${{ build.number }}`, `${{ build.branch }}`, `${{ build.commit }}`, `${{ pipeline.name }}`, `${{ project.name }}` — details of the running build
```

- [ ] **Step 5: Update rule 7**

Replace rule 7 in the `## Rules` part of `SYSTEM_PROMPT`. It currently reads:

```text
7. For notification steps, always use a configured channel name — never hardcode \
webhook URLs or bot tokens in the YAML.
```

Change it to:

```text
7. For `notify` steps, always use a configured channel name. For `http_request` \
steps, take the URL from `${{ secrets.X }}` whenever it contains a token — never hardcode \
webhook URLs or bot tokens in the YAML.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_http_request_docs.py tests/test_ai_assistant_scoped.py -q`
Expected: all pass, including `6 passed` from `test_http_request_docs.py`.

Run the whole suite: `./.venv/Scripts/python.exe -m pytest -q`
Expected: `354 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/v1/ai_assistant.py backend/tests/test_http_request_docs.py
git commit -m "feat(ai): teach the pipeline assistant the http_request step"
```

---

### Task 4: Document the step for people

**Files:**
- Modify: `frontend/src/components/pipeline/docs-panel.tsx` (one icon import, one `DOCS` entry)
- Modify: `README.md` (the built-in step list and a new example)

**Interfaces:**
- Consumes: the YAML shape from Task 1. The examples below were checked against the validator.
- Produces: an "HTTP Request" section in the in-app pipeline docs, between "Kubernetes Apply" and "Wait for Webhook".

- [ ] **Step 1: Add the docs panel section**

In `frontend/src/components/pipeline/docs-panel.tsx`, add `Send` to the `lucide-react` import list, after `Server,`:

```tsx
  Server,
  Send,
```

Then insert this entry into the `DOCS` array, between the `kube_apply` entry and the `wait_webhook` entry:

```tsx
  {
    id: "http_request",
    title: "HTTP Request",
    icon: <Send className="h-4 w-4" />,
    description:
      "Send an HTTP request to an external system: a chat webhook, a deployment or ticketing system, or your own service. Write the payload the receiver expects under json (sent as JSON), or use body for any other format and set Content-Type yourself. Placeholders always produce strings; literal numbers and booleans keep their type. The step fails the build when the status is not the expected one, retries only network errors, 5xx and 429, and does not follow redirects. The request is sent from the build agent, and the URL path, headers and body are never written to the build log. Most webhook URLs contain a token, so store the URL as a secret. Requires an agent built from this release or later.",
    yaml: `- http_request:
    url: \${{ secrets.DEPLOY_WEBHOOK_URL }}
    method: POST                 # optional: GET, POST, PUT, PATCH, DELETE (default POST)
    headers:                     # optional
      Authorization: Bearer \${{ secrets.DEPLOY_API_TOKEN }}
    json:                        # any mapping or list
      text: "Build #\${{ build.number }} of \${{ pipeline.name }} deployed"
      branch: \${{ build.branch }}
      tags: [ci, production]
    timeout: 30                  # optional seconds per attempt (default 30, max 300)
    retries: 2                   # optional extra attempts (default 0, max 5)
    expect_status: [200, 202]    # optional (default: any 2xx)
    verify_tls: true             # optional; false accepts a self-signed certificate

# Not JSON? Use body instead of json:
- http_request:
    url: https://legacy.example.com/hook
    headers:
      Content-Type: application/xml
    body: |
      <deploy><build>\${{ build.number }}</build></deploy>`,
  },
```

Inside the template literal each placeholder is written `\${{ … }}`; the backslash stops TypeScript from treating `${` as an interpolation, exactly as in the `kube_apply` entry above it.

- [ ] **Step 2: Type-check**

Run from `frontend/`: `npx tsc --noEmit`
Expected: no output and exit code 0.

- [ ] **Step 3: Update the README**

In `README.md`, in the Features list, replace the line that starts `- **11 built-in step action types**` with:

```markdown
- **12 built-in step action types** — `run` (shell), `docker_build`, `docker_login`, `docker_push`, `git_clone`, `git_pull`, `git_push`, `ssh_exec`, `kube_apply`, `http_request`, `wait_webhook`, and `wait_input`. Template interpolation resolves `${{ secrets.NAME }}` and `${{ env.NAME }}` at runtime.
```

Then, in the Pipeline Example section, insert the following immediately above the paragraph that starts `Link the pipeline to a project whose repository points at`:

````markdown
To call an external system — a chat webhook, a deployment or ticketing system, or your own service — use `http_request`. The payload has no fixed shape: write whatever the receiver expects under `json`, or use `body` for any other format.

```yaml
  - name: announce
    steps:
      - http_request:
          url: ${{ secrets.DEPLOY_WEBHOOK_URL }}
          headers:
            Authorization: Bearer ${{ secrets.DEPLOY_API_TOKEN }}
          json:
            text: "Build #${{ build.number }} of ${{ pipeline.name }} deployed"
            branch: ${{ build.branch }}
          retries: 2                 # optional; network errors, 5xx and 429 only
          expect_status: [200, 202]  # optional (default: any 2xx)
```

The request is sent from the build agent, so it can reach systems on the agent's network, and the step fails the build when the status is not the expected one. The URL path, headers and body are never written to the build log. It needs an agent built from this release or later.

````

- [ ] **Step 4: Check the docs in the browser**

Start the dev stack if it is available (`make dev-up` from the repository root), open the pipeline editor at `http://localhost:3000/pipelines/new`, open the docs panel, and expand "HTTP Request". Expected: the description and YAML example render, placeholders show as `${{ secrets.DEPLOY_WEBHOOK_URL }}` without a backslash, and the copy button copies the YAML. If the stack or a browser is not available in this environment, say so in the task report instead of claiming this step passed.

- [ ] **Step 5: Run a pipeline that uses the step**

This needs a running stack with the backend rebuilt from this branch, an agent built from this branch (`go build ./...` from `agent/`), and a request-inspection endpoint you control. If any of those is unavailable in this environment, do not run this step; report it as not done.

Create a pipeline with this definition, replacing the URL, and trigger it:

```yaml
name: http-request-check
stages:
  - name: check
    steps:
      - http_request:
          url: https://example.invalid/replace-with-your-inspection-url
          headers:
            X-Check: megooci
          json:
            text: "Build #${{ build.number }} on ${{ build.branch }}"
            count: 3
            ok: true
          retries: 1
```

Expected: the build succeeds; the receiver shows a POST with `Content-Type: application/json`, the `X-Check` header, and a JSON body in which `text` has the build number and branch filled in and `count` and `ok` are a number and a boolean. The build log shows the method, the scheme and host followed by `/…`, the status and duration, and the start of the response — and neither the URL path nor the header value.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/pipeline/docs-panel.tsx README.md
git commit -m "docs(pipeline): document the http_request step"
```
