# Pre-Execution Pipeline YAML Validation — Design

**Date:** 2026-06-22
**Status:** Approved

## Problem

A pipeline's `megooci.yaml` is never validated before a build runs, and the
three entry points that start a build each fail differently on bad YAML:

- **Manual trigger** ([builds.py `trigger_build`](../../../backend/app/api/v1/builds.py)) calls
  `parse_yaml_pipeline` uncaught — invalid YAML raises and the user gets an
  opaque HTTP 500.
- **Git webhook** ([webhooks_git.py `_create_builds`](../../../backend/app/api/v1/webhooks_git.py))
  wraps compilation in `try/except: pass` — the build is created with **no
  stages** and silently dies later, with no hint why.
- **trigger_pipeline step** ([trigger.py](../../../backend/app/services/step_actions/trigger.py))
  calls `parse_yaml_pipeline` uncaught, surfacing as an unhandled task error.

When YAML *is* parsed and an error message is produced, it is PyYAML's raw,
verbose dump rather than a clean "error on line N" message. There is a richer
semantic checker, `validate_pipeline()`, but it is **not called anywhere** in
the execution path.

The goal: **before executing a pipeline, run a syntax + structure check, and on
failure produce an error that says what is wrong and on which line.**

## Decision

Build one centralized validation function that is the single source of truth,
and call it from every place a build can start plus a live editor endpoint:

- **Scope:** YAML syntax errors (exact line + column) **and** structural
  errors (missing stage names, unknown/duplicate step types, `runs_on` rules,
  etc.), with a line number attached where determinable.
- **Enforcement points:** all execution paths (manual trigger, git webhook,
  trigger_pipeline step) and a new live-validation endpoint for the editor.
  Saving/editing a pipeline is **not** gated — invalid YAML can be saved; the
  editor surfaces problems live, and execution is blocked.
- **Failure behavior:**
  - Interactive (manual trigger): HTTP 400 with a structured error list; no
    build is created.
  - Non-interactive (webhook, trigger step): create the build, mark it
    `failed`, and record the error so it is visible in the existing build-logs
    UI.

This deliberately reuses the existing `validate_pipeline`/`_validate_step`
rules so that new step types (e.g. the recently added `kube_apply`) are
validated automatically, with no duplicated rule set.

## Component 1 — Core validation module

Lives in [pipeline_compiler.py](../../../backend/app/services/pipeline_compiler.py).

### Error model

```python
@dataclass
class PipelineError:
    message: str          # "Stage 'build' is missing a 'name'"
    line: int | None      # 1-based; None when not attributable to a line
    column: int | None    # 1-based; None for structural errors
    severity: str = "error"
```

### Entry point

`validate_pipeline_definition(yaml_content: str) -> list[PipelineError]`
(empty list = valid). Runs two phases:

**1. Syntax phase.** Parse using a line-tracking loader: a `yaml.SafeLoader`
subclass whose `construct_mapping` records each mapping node's
`start_mark.line` into a *side table* keyed by object identity — **not** into
the data dict, so the compiler's own `safe_load` stays clean and unpolluted.

- On `yaml.MarkedYAMLError`: return a **single** `PipelineError` built from
  `.problem` (plus `.context` when present) with
  `line = problem_mark.line + 1`, `column = problem_mark.column + 1`.
  Example: `"YAML syntax error on line 7, column 3: mapping values are not
  allowed here (check indentation or a missing space after ':')"`.
- Syntax errors short-circuit — unparseable YAML is not structurally checked.

**2. Structure phase.** The existing `validate_pipeline` rules, refactored to
emit `PipelineError` objects and to attach `line` from the side table for the
stage/step mapping each error belongs to (falls back to `line=None` when not
determinable). This phase inherits **all** current step types — `run`,
`write_file`, `docker_*`, `git_*`, `ssh_exec`, `kube_apply`, `wait_*`,
`copy_files`, `delete_files`, `notify`, `trigger_pipeline`, `ai_agent` — by
reusing `_validate_step`. No per-type logic is duplicated in the new module.

### Helpers and back-compat

- `assert_pipeline_valid(yaml_content)` raises
  `PipelineValidationError(errors: list[PipelineError])` for execution paths to
  catch. (Extend the existing exception to carry structured errors while
  keeping its string-list constructor working.)
- Keep `validate_pipeline() -> list[str]` and `parse_yaml_pipeline()` working
  (thin wrappers / unchanged signatures) so no existing caller breaks.

## Component 2 — Execution-path wiring

A shared guard runs **before** stages/steps are compiled.

### 2a. Manual trigger — [builds.py `trigger_build`](../../../backend/app/api/v1/builds.py)

Validate `pipeline.yaml_content` **before** creating the `Build` row. On
errors, raise `HTTPException(400)` with a structured body:

```json
{
  "detail": "Pipeline validation failed",
  "errors": [
    { "message": "YAML syntax error on line 7, column 3: ...",
      "line": 7, "column": 3, "severity": "error" }
  ]
}
```

No build is created and nothing is enqueued. The `parse_yaml_pipeline` call
that follows is then guaranteed to succeed.

### 2b/2c. Git webhook & trigger_pipeline step

- [webhooks_git.py `_create_builds`](../../../backend/app/api/v1/webhooks_git.py):
  replace `except: pass`.
- [trigger.py](../../../backend/app/services/step_actions/trigger.py):
  replace the uncaught call.

On validation errors, both:

1. Create the `Build`, set `status = "failed"`, `finished_at = now`.
2. Record the error in a **synthetic stage + step + log chunk** — stage
   `"validation"`, step `"yaml-check"` (status `failed`), one `LogChunk` whose
   content is the formatted error list (`"Line 7, col 3: ..."`). This surfaces
   in the existing build-logs UI with **no DB migration and no frontend
   change**.
3. Do **not** enqueue the build. (`execute_build` already no-ops on any
   non-`pending` build, so this is doubly safe.)
4. trigger_pipeline: the failed child build makes the parent step fail
   correctly via its existing wait-on-child logic.

A shared helper `build_validation_failure(db, build, errors)` creates the
synthetic stage/step/log so 2b and 2c stay identical.

> Note: the formatted message (with line numbers) lives in the log-chunk text
> because `Build` has no `error_message` column. Adding a first-class column is
> a possible later follow-up, out of scope here.

## Component 3 — Live validation endpoint + editor integration

### Endpoint

New route on the existing `pipelines.router` (mounted at `/pipelines`):

```
POST /api/v1/pipelines/validate
body:  { "yaml_content": "<string>" }
resp:  { "valid": true,  "errors": [] }
   or  { "valid": false, "errors": [ { "message", "line", "column", "severity" } ] }
```

- Use a **non-empty** path (`/validate`); FastAPI 0.137 rejects empty-path
  routes on no-prefix includes, and this avoids that class of issue entirely.
- Permission: `pipelines.read` (read-only lint; no DB write).
- Returns **200** whether or not the YAML is valid — invalid YAML is a normal
  result, not an HTTP error.
- New `PipelineValidationResponse` schema in
  [schemas/pipeline.py](../../../backend/app/schemas/pipeline.py).
- Cap request body size to keep the lint cheap.
- Calls the exact same `validate_pipeline_definition()` the execution paths
  use → editor, Run button, and webhooks can never disagree.

### Frontend

- Add `validatePipelineYaml(yaml_content)` to
  [api.ts](../../../frontend/src/lib/api.ts).
- In [pipeline-editor.tsx](../../../frontend/src/components/pipeline/pipeline-editor.tsx),
  call it **debounced** (~500 ms after typing stops). Render results as:
  - A **problems list** below the editor — each row `Line N: message`,
    clickable to scroll the editor to that line.
  - A CodeMirror **diagnostics underline** on the offending line/column, passed
    in via an optional `diagnostics` prop — the one small addition to the
    reusable [yaml-editor.tsx](../../../frontend/src/components/ui/yaml-editor.tsx).
- The Run/Trigger action surfaces a 400 from 2a (same error list) in a
  toast/inline panel, so line numbers reach the user even if they bypass the
  live check.

## Error-message quality

- **Syntax:** `"YAML syntax error on line {L}, column {C}: {problem}"`, with
  PyYAML's `context` appended when present. For the most common mistakes (tab
  indentation, missing space after `:`, unbalanced quotes/brackets) add a short
  hint clause.
- **Structure:** `"{message} (line {L})"` when a line is known; otherwise the
  stage/step location wording already produced today.
- Output is deterministic and ordered (a syntax error is reported first and
  alone; structural errors in document order) so it is stable and testable.

## Edge cases

- Empty / whitespace-only `yaml_content` → `"Pipeline definition is empty"`
  (line 1).
- `yaml_content is None` (pipeline never given YAML) → treated as empty; manual
  trigger 400s instead of 500ing.
- YAML valid but not a mapping/list, or `stages` not a list → existing
  structural messages, line attached where possible.
- Tab indentation → PyYAML reports it; surface the line.
- Oversized validate-endpoint body → rejected by the size cap.
- Multi-document YAML (`---`) → first document only is validated (matches
  current `safe_load` behavior); first error reported.

## Testing (TDD — tests written first)

- **Unit (`validate_pipeline_definition`):** table-driven fixtures — each
  bad-YAML sample asserts exact `line`/`column`/`message`; valid samples assert
  `[]`. Covers every edge case above plus a representative structural error per
  rule, including `kube_apply`.
- **API:** `POST /pipelines/validate` returns 200 + correct error list for
  good/bad input; permission enforced.
- **Execution paths:** manual trigger returns 400 with `errors` and creates
  **no** build; webhook + trigger_pipeline create a build marked `failed` with
  the synthetic `validation`/`yaml-check` stage carrying the line message;
  valid YAML still triggers normally (regression guard).
- **Frontend:** editor shows a problems entry with the right line for a known-
  bad sample (API mocked).

## Out of scope

- Gating pipeline create/update (saving invalid YAML stays allowed).
- A first-class `Build.error_message` column.
- Client-side reimplementation of structural rules (server stays
  authoritative; optional CodeMirror syntax-only linting is a separate UX
  nicety, not part of this work).
