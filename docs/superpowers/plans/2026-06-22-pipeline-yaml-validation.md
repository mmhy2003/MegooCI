# Pre-Execution Pipeline YAML Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before a build runs, validate the pipeline's YAML for syntax and structure, and report errors that say what is wrong and on which line.

**Architecture:** One centralized function, `validate_pipeline_definition(yaml_content)`, returns a list of structured `PipelineError` objects (message + line + column). It is the single source of truth, called from all three execution entry points (manual trigger, git webhook, trigger_pipeline step) and a new live-validation endpoint the editor calls. Interactive triggers return HTTP 400; non-interactive triggers create a build marked `failed` with the errors recorded in a synthetic stage so they show in the existing logs UI.

**Tech Stack:** Python 3 / FastAPI / PyYAML / SQLAlchemy (async) / pytest on the backend; Next.js / React / TypeScript / CodeMirror on the frontend.

## Global Constraints

- **Backend tests:** pytest with `asyncio_mode = "auto"`; run from the `backend/` directory (`cd backend && pytest ...`). `pythonpath = ["."]`, `testpaths = ["tests"]`.
- **Preserve existing messages:** the existing `validate_pipeline()` structural error strings are asserted by `backend/tests/test_pipeline_compiler.py` via substring (`in`) checks. Structural `PipelineError.message` text MUST stay byte-identical to today; the line number travels in the separate `.line` field, NOT appended to `.message`.
- **FastAPI 0.137:** the new endpoint MUST use a non-empty path (`/validate`); empty-path routes break on no-prefix includes. It is registered on the existing `pipelines.router` (mounted at `/pipelines`), giving `/api/v1/pipelines/validate`.
- **`execute_build` safety:** `execute_build` no-ops on any build whose status is not `pending` (`build_executor.py:90`). A build we pre-mark `failed` is therefore safe even if enqueued.
- **Frontend has no test runner** (no vitest/jest, no `test` script). Frontend tasks are verified with `npm run lint` and `npm run build` (Next.js build does the typecheck), run from the `frontend/` directory. Do NOT invent a test framework.
- **Commits:** end every commit message body with the trailer:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- Do all work on a feature branch (e.g. `feature/pipeline-yaml-validation`), not on `main`.

---

### Task 1: Error model, line-tracking loader, and syntax validation

**Files:**
- Modify: `backend/app/services/pipeline_compiler.py` (add imports near top; add `PipelineError`, `_LineTrackingLoader`, `_syntax_hint`, `_syntax_error_from`, `validate_pipeline_definition` near the other top-level functions)
- Test: `backend/tests/test_pipeline_validation.py` (new file)

**Interfaces:**
- Produces:
  - `@dataclass class PipelineError` with fields `message: str`, `line: int | None = None`, `column: int | None = None`, `severity: str = "error"`, and a method `to_dict() -> dict`.
  - `validate_pipeline_definition(yaml_content: str | None) -> list[PipelineError]` — in THIS task it returns the syntax error list for unparseable YAML and `[]` for anything that parses (structure checks are added in Task 2).
  - `_LineTrackingLoader(yaml.SafeLoader)` with attribute `line_map: dict[int, int]`.
  - `_syntax_error_from(exc) -> PipelineError`, `_syntax_hint(problem: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_pipeline_validation.py`:

```python
"""Tests for validate_pipeline_definition — syntax phase (Task 1)."""

from app.services.pipeline_compiler import (
    PipelineError,
    validate_pipeline_definition,
)

VALID = (
    "name: demo\n"
    "stages:\n"
    "  - name: build\n"
    "    steps:\n"
    "      - run: echo hi\n"
)


def test_valid_yaml_has_no_syntax_errors():
    assert validate_pipeline_definition(VALID) == []


def test_pipeline_error_to_dict():
    err = PipelineError(message="boom", line=3, column=5)
    assert err.to_dict() == {
        "message": "boom",
        "line": 3,
        "column": 5,
        "severity": "error",
    }


def test_bad_indentation_reports_line_and_column():
    # The mis-indented "steps:" cannot be parsed as a mapping value.
    bad = (
        "name: demo\n"
        "stages:\n"
        "  - name: build\n"
        "   steps:\n"           # 3-space indent under a 4-space block
        "      - run: echo hi\n"
    )
    errors = validate_pipeline_definition(bad)
    assert len(errors) == 1
    assert errors[0].line is not None
    assert "YAML syntax error" in errors[0].message
    assert f"line {errors[0].line}" in errors[0].message


def test_tab_indentation_is_reported():
    bad = "name: demo\nstages:\n\t- name: build\n"
    errors = validate_pipeline_definition(bad)
    assert len(errors) == 1
    assert errors[0].line is not None
    assert "YAML syntax error" in errors[0].message


def test_unclosed_quote_reports_line():
    bad = 'name: "demo\nstages: []\n'
    errors = validate_pipeline_definition(bad)
    assert len(errors) == 1
    assert errors[0].line is not None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_pipeline_validation.py -v`
Expected: FAIL with `ImportError: cannot import name 'PipelineError'` (and/or `validate_pipeline_definition`).

- [ ] **Step 3: Implement the error model, loader, and syntax phase**

At the top of `backend/app/services/pipeline_compiler.py`, add to the imports (the file already has `from typing import Any` and `import yaml`):

```python
from dataclasses import dataclass
```

Then add these definitions (place them just below the existing `STEP_TYPE_KEYS` set and above the `PipelineValidationError` class):

```python
@dataclass
class PipelineError:
    """A single validation problem. `line`/`column` are 1-based and may be
    None when the problem cannot be attributed to a specific position."""

    message: str
    line: int | None = None
    column: int | None = None
    severity: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "line": self.line,
            "column": self.column,
            "severity": self.severity,
        }


class _LineTrackingLoader(yaml.SafeLoader):
    """SafeLoader that records the 1-based source line of every mapping node,
    keyed by the id() of the constructed dict, in `self.line_map`.

    The line is stored in a side table rather than injected into the data so
    the compiler's own parse stays clean. The parsed structure must be kept
    alive while `line_map` is read (id() reuse is impossible while the objects
    live), which is exactly how validate_pipeline_definition uses it.
    """

    def __init__(self, stream: Any) -> None:
        super().__init__(stream)
        self.line_map: dict[int, int] = {}

    def construct_mapping(self, node: Any, deep: bool = False) -> dict[Any, Any]:
        mapping = super().construct_mapping(node, deep=deep)
        self.line_map[id(mapping)] = node.start_mark.line + 1
        return mapping


def _syntax_hint(problem: str) -> str:
    """A short, friendly nudge for the most common YAML mistakes."""
    p = problem.lower()
    if "could not find expected ':'" in p or "mapping values are not allowed" in p:
        return " — check indentation and that each key has a space after ':'"
    if "\\t" in p or "tab" in p:
        return " — YAML does not allow tabs for indentation; use spaces"
    if "unexpected end of stream" in p or "expected <block end>" in p:
        return " — check for an unclosed quote or bracket"
    return ""


def _syntax_error_from(exc: yaml.MarkedYAMLError) -> PipelineError:
    mark = getattr(exc, "problem_mark", None)
    line = (mark.line + 1) if mark is not None else None
    column = (mark.column + 1) if mark is not None else None
    problem = (getattr(exc, "problem", None) or "could not parse YAML").strip()
    context = getattr(exc, "context", None)
    where = f" on line {line}, column {column}" if line is not None else ""
    ctx = f" ({context})" if context else ""
    return PipelineError(
        message=f"YAML syntax error{where}: {problem}{ctx}{_syntax_hint(problem)}",
        line=line,
        column=column,
    )


def validate_pipeline_definition(yaml_content: str | None) -> list[PipelineError]:
    """Validate a pipeline YAML string. Returns a list of structured errors
    (empty = valid). Syntax errors short-circuit structural checks.

    NOTE (Task 1): only the syntax phase is implemented here; Task 2 replaces
    the trailing `return []` with the structure phase.
    """
    loader = _LineTrackingLoader(yaml_content or "")
    try:
        try:
            data = loader.get_single_data()
        except yaml.MarkedYAMLError as exc:
            return [_syntax_error_from(exc)]
        except yaml.YAMLError as exc:  # pragma: no cover - defensive
            return [PipelineError(message=f"YAML syntax error: {exc}")]
        line_map = dict(loader.line_map)
    finally:
        loader.dispose()

    _ = (data, line_map)  # consumed by the structure phase in Task 2
    return []
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_pipeline_validation.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pipeline_compiler.py backend/tests/test_pipeline_validation.py
git commit -m "$(cat <<'EOF'
feat(pipelines): structured PipelineError + line-aware YAML syntax validation

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Structure phase + back-compat wrapper

**Files:**
- Modify: `backend/app/services/pipeline_compiler.py` (add `_structure_errors`; change the trailing `return []` in `validate_pipeline_definition` to call it; replace the body of the existing `validate_pipeline` with a wrapper)
- Test: `backend/tests/test_pipeline_validation.py` (add cases)

**Interfaces:**
- Consumes: `PipelineError`, `validate_pipeline_definition`, existing module-level `_validate_runs_on(value) -> list[str]` and `_validate_step(step, stage_name, step_index) -> list[str]` (UNCHANGED).
- Produces: `_structure_errors(data: Any, line_map: dict[int, int]) -> list[PipelineError]`; `validate_pipeline_definition` now returns syntax OR structure errors; `validate_pipeline(yaml_content) -> list[str]` returns `[e.message for e in validate_pipeline_definition(...)]`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_pipeline_validation.py`:

```python
def test_missing_stage_name_has_line():
    bad = (
        "name: demo\n"
        "stages:\n"
        "  - steps:\n"
        "      - run: echo hi\n"
    )
    errors = validate_pipeline_definition(bad)
    assert any("missing a 'name'" in e.message for e in errors)
    err = next(e for e in errors if "missing a 'name'" in e.message)
    assert err.line == 3  # the line of the stage mapping ("- steps:")


def test_empty_input_reports_empty():
    errors = validate_pipeline_definition("")
    assert len(errors) == 1
    assert errors[0].message == "Empty pipeline definition"


def test_none_input_reports_empty():
    errors = validate_pipeline_definition(None)
    assert len(errors) == 1
    assert errors[0].message == "Empty pipeline definition"


def test_kube_apply_missing_kubeconfig_has_line():
    bad = (
        "name: demo\n"
        "stages:\n"
        "  - name: deploy\n"
        "    steps:\n"
        "      - kube_apply:\n"
        "          manifests:\n"
        "            - k8s/\n"
    )
    errors = validate_pipeline_definition(bad)
    match = [e for e in errors if "requires 'kubeconfig'" in e.message]
    assert match, "kube_apply rule did not fire"
    assert match[0].line == 5  # the step mapping line


def test_backcompat_validate_pipeline_returns_strings():
    from app.services.pipeline_compiler import validate_pipeline

    bad = (
        "name: demo\n"
        "stages:\n"
        "  - name: deploy\n"
        "    steps:\n"
        "      - kube_apply:\n"
        "          manifests:\n"
        "            - k8s/\n"
    )
    errors = validate_pipeline(bad)
    assert all(isinstance(e, str) for e in errors)
    assert any("requires 'kubeconfig'" in e for e in errors)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_pipeline_validation.py -v`
Expected: FAIL — `test_missing_stage_name_has_line`, `test_empty_input_reports_empty`, `test_none_input_reports_empty`, `test_kube_apply_missing_kubeconfig_has_line` fail (structure phase returns `[]`); `test_backcompat_*` passes only if `validate_pipeline` already routes through the new code (it does not yet).

- [ ] **Step 3: Add the structure phase and rewire the wrapper**

In `backend/app/services/pipeline_compiler.py`, replace the last two lines of `validate_pipeline_definition`:

```python
    _ = (data, line_map)  # consumed by the structure phase in Task 2
    return []
```

with:

```python
    return _structure_errors(data, line_map)
```

Add `_structure_errors` immediately after `validate_pipeline_definition`. It is the existing `validate_pipeline` orchestration, operating on already-parsed `data` and wrapping each message in a `PipelineError` with the right line. Message text is copied verbatim from the current code:

```python
def _structure_errors(data: Any, line_map: dict[int, int]) -> list[PipelineError]:
    errors: list[PipelineError] = []

    if data is None:
        return [PipelineError(message="Empty pipeline definition", line=1)]

    if isinstance(data, list):
        data = {"stages": data}

    if not isinstance(data, dict):
        return [
            PipelineError(
                message="Pipeline definition must be a mapping or a list of stages",
                line=1,
            )
        ]

    top_line = line_map.get(id(data))

    runs_on = data.get("runs_on")
    if runs_on is not None:
        for msg in _validate_runs_on(runs_on):
            errors.append(PipelineError(message=msg, line=top_line))

    stages = data.get("stages")
    if not stages:
        errors.append(
            PipelineError(message="Pipeline must define at least one stage", line=top_line)
        )
        return errors

    if not isinstance(stages, list):
        errors.append(PipelineError(message="'stages' must be a list", line=top_line))
        return errors

    stage_names: set[str] = set()
    for i, stage in enumerate(stages):
        if not isinstance(stage, dict):
            errors.append(PipelineError(message=f"Stage {i} must be a mapping"))
            continue

        stage_line = line_map.get(id(stage))
        name = stage.get("name")
        if not name:
            errors.append(
                PipelineError(message=f"Stage {i} is missing a 'name' field", line=stage_line)
            )
        elif name in stage_names:
            errors.append(
                PipelineError(message=f"Duplicate stage name: '{name}'", line=stage_line)
            )
        else:
            stage_names.add(name)

        steps = stage.get("steps")
        if not steps:
            errors.append(
                PipelineError(
                    message=f"Stage '{name or i}' must define at least one step",
                    line=stage_line,
                )
            )
        elif not isinstance(steps, list):
            errors.append(
                PipelineError(
                    message=f"Stage '{name or i}': 'steps' must be a list", line=stage_line
                )
            )
        else:
            for j, step in enumerate(steps):
                if isinstance(step, str):
                    continue
                if not isinstance(step, dict):
                    errors.append(
                        PipelineError(
                            message=f"Stage '{name or i}', step {j}: must be a string or mapping"
                        )
                    )
                    continue
                step_line = line_map.get(id(step))
                for msg in _validate_step(step, stage_name=name or str(i), step_index=j):
                    errors.append(PipelineError(message=msg, line=step_line))

        when = stage.get("when")
        if when is not None and not isinstance(when, dict):
            errors.append(
                PipelineError(
                    message=f"Stage '{name or i}': 'when' must be a mapping", line=stage_line
                )
            )

        if "runs_on" in stage:
            errors.append(
                PipelineError(
                    message=(
                        f"Stage '{name or i}': 'runs_on' is a pipeline-level field; "
                        f"move it to the top of the YAML, not inside the stage."
                    ),
                    line=stage_line,
                )
            )

    return errors
```

Now replace the ENTIRE body of the existing `validate_pipeline` function (the one that begins `def validate_pipeline(yaml_content: str) -> list[str]:` and contains its own `yaml.safe_load`) with a thin wrapper:

```python
def validate_pipeline(yaml_content: str) -> list[str]:
    """Back-compat: returns just the messages from validate_pipeline_definition."""
    return [e.message for e in validate_pipeline_definition(yaml_content)]
```

- [ ] **Step 4: Run the new tests and the full compiler suite**

Run: `cd backend && pytest tests/test_pipeline_validation.py tests/test_pipeline_compiler.py -v`
Expected: PASS for all tests in both files (the existing `test_pipeline_compiler.py` substring assertions still hold because message text is unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pipeline_compiler.py backend/tests/test_pipeline_validation.py
git commit -m "$(cat <<'EOF'
feat(pipelines): line-aware structural validation; route validate_pipeline through it

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Structured exception + assert helper

**Files:**
- Modify: `backend/app/services/pipeline_compiler.py` (extend `PipelineValidationError.__init__`; add `assert_pipeline_valid`)
- Test: `backend/tests/test_pipeline_validation.py` (add cases)

**Interfaces:**
- Consumes: `PipelineError`, `validate_pipeline_definition`.
- Produces: `PipelineValidationError` whose `.errors` is `list[PipelineError]` (accepts either `list[str]` legacy or `list[PipelineError]`); `assert_pipeline_valid(yaml_content: str | None) -> None` raises `PipelineValidationError` when invalid.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_pipeline_validation.py`:

```python
def test_assert_pipeline_valid_raises_with_structured_errors():
    import pytest

    from app.services.pipeline_compiler import (
        PipelineError,
        PipelineValidationError,
        assert_pipeline_valid,
    )

    with pytest.raises(PipelineValidationError) as exc_info:
        assert_pipeline_valid("name: demo\nstages: []\n")

    errors = exc_info.value.errors
    assert errors and all(isinstance(e, PipelineError) for e in errors)


def test_assert_pipeline_valid_passes_for_good_yaml():
    from app.services.pipeline_compiler import assert_pipeline_valid

    assert assert_pipeline_valid(VALID) is None


def test_validation_error_accepts_legacy_string_list():
    from app.services.pipeline_compiler import (
        PipelineError,
        PipelineValidationError,
    )

    err = PipelineValidationError(["Invalid YAML: boom"])
    assert len(err.errors) == 1
    assert isinstance(err.errors[0], PipelineError)
    assert err.errors[0].message == "Invalid YAML: boom"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && pytest tests/test_pipeline_validation.py -k "assert_pipeline_valid or legacy_string" -v`
Expected: FAIL with `ImportError: cannot import name 'assert_pipeline_valid'` and the legacy test failing on `isinstance(... PipelineError)` (currently `.errors` holds raw strings).

- [ ] **Step 3: Extend the exception and add the helper**

Replace the existing `PipelineValidationError` class:

```python
class PipelineValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Pipeline validation failed: {'; '.join(errors)}")
```

with one that normalizes to `PipelineError` (keep it defined AFTER `PipelineError`, which Task 1 placed above it):

```python
class PipelineValidationError(Exception):
    def __init__(self, errors: list) -> None:
        # Accept legacy list[str] or list[PipelineError].
        normalized: list[PipelineError] = [
            e if isinstance(e, PipelineError) else PipelineError(message=str(e))
            for e in errors
        ]
        self.errors: list[PipelineError] = normalized
        super().__init__(
            "Pipeline validation failed: "
            + "; ".join(e.message for e in normalized)
        )
```

Add `assert_pipeline_valid` just below `validate_pipeline_definition`:

```python
def assert_pipeline_valid(yaml_content: str | None) -> None:
    """Raise PipelineValidationError if the pipeline YAML is invalid."""
    errors = validate_pipeline_definition(yaml_content)
    if errors:
        raise PipelineValidationError(errors)
```

> If the `PipelineValidationError` class currently sits ABOVE the `PipelineError` definition from Task 1, move the `PipelineError` dataclass above `PipelineValidationError` so the type name resolves; the `isinstance` check runs at call time but keeping definition order clean avoids confusion.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && pytest tests/test_pipeline_validation.py -v`
Expected: PASS (all tests). Also run `cd backend && pytest -q` to confirm nothing else regressed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pipeline_compiler.py backend/tests/test_pipeline_validation.py
git commit -m "$(cat <<'EOF'
feat(pipelines): structured PipelineValidationError + assert_pipeline_valid helper

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Gate the manual trigger (HTTP 400)

**Files:**
- Modify: `backend/app/api/v1/builds.py` (import `validate_pipeline_definition`; add a validation gate in `trigger_build` before the `Build(...)` is constructed)
- Test: `backend/tests/test_trigger_validation.py` (new file)

**Interfaces:**
- Consumes: `validate_pipeline_definition`, `PipelineError.to_dict`.
- Produces: `trigger_build` raises `HTTPException(400, detail={"message": "Pipeline validation failed", "errors": [PipelineError.to_dict(), ...]})` for invalid YAML, before any DB row is created.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_trigger_validation.py`. It uses the same in-memory SQLite + `@compiles` shim and side-effect patching as `tests/test_build_retry.py`:

```python
"""trigger_build must reject invalid pipeline YAML with a 400 and create no build."""

import os
import sqlite3
import sys
import types
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "TEXT"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Pipeline.__table__.create(c))
        await conn.run_sync(lambda c: Build.__table__.create(c))
        await conn.run_sync(lambda c: Stage.__table__.create(c))
        await conn.run_sync(lambda c: Step.__table__.create(c))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_side_effects(monkeypatch):
    async def _noop_async(*a, **k):
        return None

    monkeypatch.setattr("app.api.v1.builds.run_build.delay", lambda *a, **k: None)

    stub_search = types.ModuleType("app.services.search")
    stub_search.index_build = _noop_async
    monkeypatch.setitem(sys.modules, "app.services.search", stub_search)

    stub_notif = types.ModuleType("app.services.in_app_notifications")
    stub_notif.publish_build_update = _noop_async
    monkeypatch.setitem(sys.modules, "app.services.in_app_notifications", stub_notif)


async def _seed_pipeline(sf, yaml_content: str):
    from app.models.pipeline import Pipeline

    pid = uuid.uuid4()
    async with sf() as db:
        db.add(
            Pipeline(
                id=pid,
                project_id=uuid.uuid4(),
                name="p",
                default_branch="main",
                yaml_content=yaml_content,
                enabled=True,
                created_by=uuid.uuid4(),
            )
        )
        await db.commit()
    return pid


BAD_YAML = "name: demo\nstages:\n  - steps:\n      - run: echo hi\n"  # stage missing name
GOOD_YAML = "name: demo\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"


async def test_trigger_rejects_invalid_yaml(session_factory):
    from app.api.v1.builds import trigger_build
    from app.models.build import Build
    from app.schemas.build import BuildTriggerRequest

    pid = await _seed_pipeline(session_factory, BAD_YAML)
    user = types.SimpleNamespace(id=uuid.uuid4())

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await trigger_build(pid, BuildTriggerRequest(), db=db, current_user=user)

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert detail["message"] == "Pipeline validation failed"
    assert any("missing a 'name'" in e["message"] for e in detail["errors"])

    async with session_factory() as db:
        count = await db.scalar(select(func.count()).select_from(Build))
    assert count == 0, "no build should be created when YAML is invalid"


async def test_trigger_accepts_valid_yaml(session_factory):
    from app.api.v1.builds import trigger_build
    from app.schemas.build import BuildTriggerRequest

    pid = await _seed_pipeline(session_factory, GOOD_YAML)
    user = types.SimpleNamespace(id=uuid.uuid4())

    async with session_factory() as db:
        build = await trigger_build(pid, BuildTriggerRequest(), db=db, current_user=user)

    assert build.status == "pending"
    assert build.number == 1
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_trigger_validation.py -v`
Expected: FAIL — `test_trigger_rejects_invalid_yaml` fails (no 400 raised; build gets created or a 500-style error from the later uncaught parse), because the gate does not exist yet.

- [ ] **Step 3: Add the validation gate**

In `backend/app/api/v1/builds.py`, extend the existing import block:

```python
from app.services.pipeline_compiler import (
    compile_to_build_graph,
    normalize_runs_on,
    parse_yaml_pipeline,
    validate_pipeline_definition,
)
```

In `trigger_build`, immediately AFTER the `if not pipeline.enabled:` block and BEFORE the `max_number = await db.scalar(...)` line, insert:

```python
    # Validate the pipeline YAML before doing any work. Invalid YAML must not
    # create a build — surface the line-level errors to the caller instead.
    if pipeline.yaml_content:
        validation_errors = validate_pipeline_definition(pipeline.yaml_content)
        if validation_errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Pipeline validation failed",
                    "errors": [e.to_dict() for e in validation_errors],
                },
            )
```

(The existing `if pipeline.yaml_content:` compile block further down is unchanged; its `parse_yaml_pipeline` call is now guaranteed to succeed.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_trigger_validation.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/builds.py backend/tests/test_trigger_validation.py
git commit -m "$(cat <<'EOF'
feat(builds): reject manual trigger of invalid pipeline YAML with 400 + line errors

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Shared "record validation failure" helper

**Files:**
- Create: `backend/app/services/build_validation.py`
- Test: `backend/tests/test_build_validation.py` (new file)

**Interfaces:**
- Consumes: `PipelineError`, models `Build`, `Stage`, `Step`, `LogChunk`.
- Produces:
  - `format_validation_errors(errors: list[PipelineError]) -> str`
  - `async record_pipeline_validation_failure(db: AsyncSession, build: Build, errors: list[PipelineError]) -> None` — sets `build.status="failed"`, `build.finished_at=now`, and adds a `validation` stage + `yaml-check` step (failed) + one `LogChunk` containing the formatted errors.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_validation.py`:

```python
"""record_pipeline_validation_failure attaches a visible failed stage to a build."""

import sqlite3
import uuid

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "TEXT"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, LogChunk, Stage, Step

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for model in (Build, Stage, Step, LogChunk):
            await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def test_format_validation_errors_includes_line():
    from app.services.build_validation import format_validation_errors
    from app.services.pipeline_compiler import PipelineError

    text = format_validation_errors(
        [
            PipelineError(message="bad indent", line=7, column=3),
            PipelineError(message="missing name", line=12),
            PipelineError(message="no line here"),
        ]
    )
    assert "Line 7, col 3: bad indent" in text
    assert "Line 12: missing name" in text
    assert "no line here" in text


async def test_records_failed_validation_stage(session_factory):
    from app.models.build import Build, Stage, Step
    from app.services.build_validation import record_pipeline_validation_failure
    from app.services.pipeline_compiler import PipelineError

    build_id = uuid.uuid4()
    async with session_factory() as db:
        build = Build(
            id=build_id, pipeline_id=uuid.uuid4(), number=1,
            status="pending", trigger_type="push",
        )
        db.add(build)
        await db.flush()

        await record_pipeline_validation_failure(
            db, build, [PipelineError(message="bad indent", line=7, column=3)]
        )
        await db.commit()

    async with session_factory() as db:
        reloaded = await db.get(Build, build_id)
        assert reloaded.status == "failed"
        assert reloaded.finished_at is not None

        stage = (await db.execute(select(Stage))).scalar_one()
        assert stage.name == "validation"
        assert stage.status == "failed"

        step = (await db.execute(select(Step))).scalar_one()
        assert step.name == "yaml-check"
        assert step.status == "failed"

    from app.models.build import LogChunk

    async with session_factory() as db:
        chunk = (await db.execute(select(LogChunk))).scalar_one()
        assert "Line 7, col 3: bad indent" in chunk.content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_build_validation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.build_validation'`.

- [ ] **Step 3: Implement the helper**

Create `backend/app/services/build_validation.py`:

```python
"""Record a pipeline-validation failure onto an already-created build.

Used by the non-interactive trigger paths (git webhook, trigger_pipeline step),
where there is no user to return an HTTP 400 to. We still create the build,
mark it failed, and attach the validation errors as a synthetic
``validation`` / ``yaml-check`` stage so they appear in the build-logs UI with
no schema migration.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import Build, LogChunk, Stage, Step
from app.services.pipeline_compiler import PipelineError


def format_validation_errors(errors: list[PipelineError]) -> str:
    lines: list[str] = []
    for e in errors:
        if e.line is not None and e.column is not None:
            lines.append(f"Line {e.line}, col {e.column}: {e.message}")
        elif e.line is not None:
            lines.append(f"Line {e.line}: {e.message}")
        else:
            lines.append(e.message)
    return "\n".join(lines)


async def record_pipeline_validation_failure(
    db: AsyncSession, build: Build, errors: list[PipelineError]
) -> None:
    now = datetime.now(timezone.utc)
    build.status = "failed"
    build.finished_at = now

    stage = Stage(build_id=build.id, name="validation", status="failed", sort_order=0)
    db.add(stage)
    await db.flush()

    step = Step(
        stage_id=stage.id,
        name="yaml-check",
        step_type="run",
        status="failed",
        exit_code=1,
        sort_order=0,
    )
    db.add(step)
    await db.flush()

    content = "Pipeline validation failed:\n" + format_validation_errors(errors) + "\n"
    db.add(
        LogChunk(step_id=step.id, seq=0, timestamp=now, stream="stderr", content=content)
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_build_validation.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/build_validation.py backend/tests/test_build_validation.py
git commit -m "$(cat <<'EOF'
feat(builds): helper to record pipeline-validation failure as a visible build stage

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire validation into the git-webhook path

**Files:**
- Modify: `backend/app/api/v1/webhooks_git.py` (`_enqueue_matching_builds` — add `validate_pipeline_definition` to the local import; replace the `try/except: pass` compile block)
- Test: `backend/tests/test_webhook_validation.py` (new file)

**Interfaces:**
- Consumes: `validate_pipeline_definition`, `record_pipeline_validation_failure`.
- Produces: `_enqueue_matching_builds` creates a `failed` build with a `validation` stage when a matched pipeline's YAML is invalid, instead of an empty stageless build.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_webhook_validation.py`:

```python
"""A git-webhook build for a pipeline with invalid YAML is marked failed with
a visible validation stage (not a silent stageless build)."""

import uuid

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "TEXT"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, LogChunk, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for model in (Pipeline, Build, Stage, Step, LogChunk):
            await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


BAD_YAML = "name: demo\nstages:\n  - steps:\n      - run: echo hi\n"  # missing name


async def test_webhook_invalid_yaml_creates_failed_build(session_factory):
    import types

    from app.api.v1.webhooks_git import _enqueue_matching_builds
    from app.models.build import Build, Stage
    from app.models.pipeline import Pipeline

    project_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            Pipeline(
                id=uuid.uuid4(), project_id=project_id, name="p",
                default_branch="main", yaml_content=BAD_YAML,
                enabled=True, created_by=uuid.uuid4(),
                source_repo_url="https://example.com/r.git",
            )
        )
        await db.commit()

        repo = types.SimpleNamespace(
            project_id=project_id, id=uuid.uuid4(),
            repo_url="https://example.com/r.git",
        )
        event = types.SimpleNamespace(branch="main", commit_sha="abc123")

        ids = await _enqueue_matching_builds(db, repo, event)
        await db.commit()

    assert len(ids) == 1
    async with session_factory() as db:
        build = await db.get(Build, ids[0])
        assert build.status == "failed"
        stages = (await db.execute(select(Stage).where(Stage.build_id == ids[0]))).scalars().all()
        assert any(s.name == "validation" for s in stages)
```

> The stubs above are exact: `_enqueue_matching_builds` reads only `repo.project_id`, `repo.id`, `repo.repo_url` and `event.branch`, `event.commit_sha`. The seeded pipeline matches via `source_repo_url == repo.repo_url`, and its `default_branch` ("main") equals `event.branch` so the branch filter passes. The invalid-YAML build creates only the synthetic `validation` stage (no `artifact_paths` list is bound), so the `ARRAY → "TEXT"` mapping is sufficient.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_webhook_validation.py -v`
Expected: FAIL — the build is created but has NO `validation` stage and its status is not `failed` (current code swallows the compile error and leaves a stageless `pending` build).

- [ ] **Step 3: Wire in validation**

In `backend/app/api/v1/webhooks_git.py`, extend the local import inside `_enqueue_matching_builds`:

```python
    from app.services.pipeline_compiler import (
        compile_to_build_graph,
        normalize_runs_on,
        parse_yaml_pipeline,
        validate_pipeline_definition,
    )
```

Replace the existing block (the one that begins `if pipeline.yaml_content:` followed by `try:` … and ends with `except Exception: pass`) with:

```python
        if pipeline.yaml_content:
            validation_errors = validate_pipeline_definition(pipeline.yaml_content)
            if validation_errors:
                from app.services.build_validation import (
                    record_pipeline_validation_failure,
                )

                await record_pipeline_validation_failure(db, build, validation_errors)
            else:
                pipeline_def = parse_yaml_pipeline(pipeline.yaml_content)
                build.runs_on = normalize_runs_on(pipeline_def.get("runs_on"))
                stage_defs = compile_to_build_graph(pipeline_def)

                for sort_order, stage_def in enumerate(stage_defs):
                    stage = Stage(
                        build_id=build.id,
                        name=stage_def["name"],
                        status="pending",
                        sort_order=sort_order,
                        artifact_paths=stage_def.get("artifacts"),
                    )
                    db.add(stage)
                    await db.flush()

                    for step_order, step_def in enumerate(stage_def.get("steps", [])):
                        step_type = step_def.get("step_type", "run")
                        config = step_def.get("config", {})
                        command = config.get("command") if step_type == "run" else None

                        step = Step(
                            stage_id=stage.id,
                            name=step_def.get("name", f"step-{step_order}"),
                            step_type=step_type,
                            command=command,
                            config_json=config if config else None,
                            status="pending",
                            sort_order=step_order,
                        )
                        db.add(step)
```

(A failed build is still appended to `new_build_ids`. Dispatching it is harmless: `execute_build` no-ops on a non-`pending` build — see Global Constraints.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_webhook_validation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/webhooks_git.py backend/tests/test_webhook_validation.py
git commit -m "$(cat <<'EOF'
fix(webhooks): record invalid-YAML webhook builds as failed with a visible stage

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Wire validation into the trigger_pipeline step

**Files:**
- Modify: `backend/app/services/step_actions/trigger.py` (`TriggerPipelineHandler.execute` — import the helpers; validate `target.yaml_content` before compiling)
- Test: `backend/tests/test_trigger_step_validation.py` (new file)

**Interfaces:**
- Consumes: `validate_pipeline_definition`, `record_pipeline_validation_failure`, `format_validation_errors`.
- Produces: when the target pipeline's YAML is invalid, the step records a failed child build and yields a failed `StepResult`, so the parent step fails with a clear message.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_trigger_step_validation.py`:

```python
"""trigger_pipeline targeting a pipeline with invalid YAML fails the step and
records a failed child build."""

import uuid

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "TEXT"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, LogChunk, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for model in (Pipeline, Build, Stage, Step, LogChunk):
            await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


BAD_YAML = "name: child\nstages:\n  - steps:\n      - run: echo hi\n"  # missing name


async def test_trigger_step_fails_on_invalid_target_yaml(session_factory):
    from app.models.build import Build, Stage
    from app.models.pipeline import Pipeline
    from app.services.step_actions.base import StepContext, StepResult
    from app.services.step_actions.trigger import TriggerPipelineHandler

    target_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            Pipeline(
                id=target_id, project_id=uuid.uuid4(), name="child",
                default_branch="main", yaml_content=BAD_YAML,
                enabled=True, created_by=uuid.uuid4(),
            )
        )
        await db.commit()

        # The invalid-YAML path returns before reading ctx, but build a real
        # StepContext (all fields required, no defaults) so nothing is left to chance.
        ctx = StepContext(
            build_id=uuid.uuid4(), step_id=uuid.uuid4(), step_name="trigger",
            stage_name="deploy", pipeline_id=uuid.uuid4(), project_id=uuid.uuid4(),
        )
        handler = TriggerPipelineHandler()

        results = []
        async for item in handler.execute({"pipeline": str(target_id)}, ctx, db):
            results.append(item)

        final = [r for r in results if isinstance(r, StepResult)][-1]
        assert final.status == "failed"

        builds = (await db.execute(select(Build))).scalars().all()
        assert len(builds) == 1 and builds[0].status == "failed"
        stages = (await db.execute(select(Stage))).scalars().all()
        assert any(s.name == "validation" for s in stages)
```

> Mirror the real `StepContext` shape that `TriggerPipelineHandler.execute` reads (the stub `ctx` only needs the attributes the handler touches before/around build creation). Read the handler in full first.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_trigger_step_validation.py -v`
Expected: FAIL — currently `parse_yaml_pipeline` is uncaught, so the handler raises rather than yielding a failed `StepResult`, OR a child build is created without a `validation` stage.

- [ ] **Step 3: Wire in validation**

In `backend/app/services/step_actions/trigger.py`, extend the top-level import:

```python
from app.services.pipeline_compiler import (
    compile_to_build_graph,
    parse_yaml_pipeline,
    validate_pipeline_definition,
)
```

In `TriggerPipelineHandler.execute`, replace the existing block that begins `if target.yaml_content:` and runs `parse_yaml_pipeline` / `compile_to_build_graph` with:

```python
        if target.yaml_content:
            validation_errors = validate_pipeline_definition(target.yaml_content)
            if validation_errors:
                from app.services.build_validation import (
                    format_validation_errors,
                    record_pipeline_validation_failure,
                )

                await record_pipeline_validation_failure(
                    db, child_build, validation_errors
                )
                await db.commit()
                detail = format_validation_errors(validation_errors)
                yield LogLine(
                    stream="stderr",
                    content=(
                        f"Error: target pipeline '{target.name}' has invalid YAML:\n"
                        f"{detail}\n"
                    ),
                )
                yield StepResult(
                    exit_code=1,
                    status="failed",
                    error=f"Target pipeline '{target.name}' has invalid YAML",
                )
                return

            pipeline_def = parse_yaml_pipeline(target.yaml_content)
            stage_defs = compile_to_build_graph(pipeline_def)

            for sort_order, stage_def in enumerate(stage_defs):
                stage = Stage(
                    build_id=child_build.id,
                    name=stage_def["name"],
                    status="pending",
                    sort_order=sort_order,
                )
                db.add(stage)
                await db.flush()

                for step_order, step_def in enumerate(stage_def.get("steps", [])):
                    step_type = step_def.get("step_type", "run")
                    step_config = step_def.get("config", {})
                    command = step_config.get("command") if step_type == "run" else None

                    step = Step(
                        stage_id=stage.id,
                        name=step_def.get("name", f"step-{step_order}"),
                        step_type=step_type,
                        command=command,
                        config_json=step_config if step_config else None,
                        status="pending",
                        sort_order=step_order,
                    )
                    db.add(step)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_trigger_step_validation.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/step_actions/trigger.py backend/tests/test_trigger_step_validation.py
git commit -m "$(cat <<'EOF'
fix(trigger_pipeline): fail the step and record a failed child build on invalid target YAML

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Live validation endpoint + schemas

**Files:**
- Modify: `backend/app/schemas/pipeline.py` (add request/response schemas)
- Modify: `backend/app/api/v1/pipelines.py` (add `POST /validate` route)
- Test: `backend/tests/test_validate_endpoint.py` (new file)

**Interfaces:**
- Consumes: `validate_pipeline_definition`, `PipelineError.to_dict`.
- Produces:
  - Schemas `PipelineValidateRequest{yaml_content: str = ""}`, `PipelineErrorItem{message, line, column, severity}`, `PipelineValidationResponse{valid: bool, errors: list[PipelineErrorItem]}`.
  - `validate_pipeline_yaml(body, _current_user) -> PipelineValidationResponse` handler at `POST /api/v1/pipelines/validate`, permission `pipelines.read`, returns 200 for valid and invalid YAML; 413 if the body exceeds 256 KiB.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_validate_endpoint.py`:

```python
"""POST /pipelines/validate returns structured results for good and bad YAML."""

import pytest
from fastapi import HTTPException

GOOD = "name: demo\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"
BAD = "name: demo\nstages:\n  - name: build\n   steps:\n      - run: echo hi\n"  # bad indent


async def test_validate_endpoint_accepts_valid():
    from app.api.v1.pipelines import validate_pipeline_yaml
    from app.schemas.pipeline import PipelineValidateRequest

    resp = await validate_pipeline_yaml(
        PipelineValidateRequest(yaml_content=GOOD), _current_user=None
    )
    assert resp.valid is True
    assert resp.errors == []


async def test_validate_endpoint_reports_line_for_bad_syntax():
    from app.api.v1.pipelines import validate_pipeline_yaml
    from app.schemas.pipeline import PipelineValidateRequest

    resp = await validate_pipeline_yaml(
        PipelineValidateRequest(yaml_content=BAD), _current_user=None
    )
    assert resp.valid is False
    assert resp.errors
    assert resp.errors[0].line is not None
    assert "YAML syntax error" in resp.errors[0].message


async def test_validate_endpoint_rejects_oversized_body():
    from app.api.v1.pipelines import validate_pipeline_yaml
    from app.schemas.pipeline import PipelineValidateRequest

    huge = "a: 1\n" * 100_000  # > 256 KiB
    with pytest.raises(HTTPException) as exc_info:
        await validate_pipeline_yaml(
            PipelineValidateRequest(yaml_content=huge), _current_user=None
        )
    assert exc_info.value.status_code == 413
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && pytest tests/test_validate_endpoint.py -v`
Expected: FAIL with `ImportError: cannot import name 'validate_pipeline_yaml'` / `PipelineValidateRequest`.

- [ ] **Step 3: Add schemas**

Append to `backend/app/schemas/pipeline.py`:

```python
class PipelineValidateRequest(BaseModel):
    yaml_content: str = ""


class PipelineErrorItem(BaseModel):
    message: str
    line: int | None = None
    column: int | None = None
    severity: str = "error"


class PipelineValidationResponse(BaseModel):
    valid: bool
    errors: list[PipelineErrorItem]
```

- [ ] **Step 4: Add the route**

In `backend/app/api/v1/pipelines.py`, extend the schema import and add the compiler import:

```python
from app.schemas.pipeline import (
    PipelineCreate,
    PipelineErrorItem,
    PipelineResponse,
    PipelineUpdate,
    PipelineValidateRequest,
    PipelineValidationResponse,
)
from app.services.pipeline_compiler import validate_pipeline_definition
```

Add a module-level constant near the top (after `router = APIRouter()`):

```python
_MAX_VALIDATE_BYTES = 256 * 1024
```

Add the route (place it just after `create_pipeline`, before `get_pipeline` — a static `/validate` path does not collide with the `GET /{pipeline_id}` route):

```python
@router.post("/validate", response_model=PipelineValidationResponse)
async def validate_pipeline_yaml(
    body: PipelineValidateRequest,
    _current_user: User = Depends(require_permission("pipelines.read")),
) -> PipelineValidationResponse:
    """Lint a pipeline YAML string. Returns 200 with the error list whether or
    not the YAML is valid (invalid YAML is a normal result, not an HTTP error)."""
    if len(body.yaml_content.encode("utf-8")) > _MAX_VALIDATE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Pipeline YAML is too large to validate",
        )
    errors = validate_pipeline_definition(body.yaml_content)
    return PipelineValidationResponse(
        valid=not errors,
        errors=[PipelineErrorItem(**e.to_dict()) for e in errors],
    )
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend && pytest tests/test_validate_endpoint.py -v`
Expected: PASS (3 tests). Then run the full backend suite: `cd backend && pytest -q` — expected: all green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/pipeline.py backend/app/api/v1/pipelines.py backend/tests/test_validate_endpoint.py
git commit -m "$(cat <<'EOF'
feat(pipelines): POST /pipelines/validate live YAML lint endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Frontend API client + structured error formatting

**Files:**
- Modify: `frontend/src/lib/api.ts` (add validation types; add `validate` to `pipelinesApi`; enhance `extractErrorMessage`)

**Interfaces:**
- Produces:
  - `interface PipelineValidationError { message: string; line: number | null; column: number | null; severity: string }`
  - `interface PipelineValidationResult { valid: boolean; errors: PipelineValidationError[] }`
  - `pipelinesApi.validate(yaml_content: string): Promise<PipelineValidationResult>`
  - `extractErrorMessage` now formats a `detail` object that carries an `errors` array into `"Line N: message; ..."`.

> No frontend test runner exists (see Global Constraints). Verify with `npm run lint` and `npm run build`.

- [ ] **Step 1: Add the validation types and API method**

Near the other pipeline types/`pipelinesApi` object in `frontend/src/lib/api.ts`, add the interfaces:

```ts
export interface PipelineValidationError {
  message: string;
  line: number | null;
  column: number | null;
  severity: string;
}

export interface PipelineValidationResult {
  valid: boolean;
  errors: PipelineValidationError[];
}
```

Add this method inside the existing `pipelinesApi` object (alongside `list`/`get`/`delete`/etc.):

```ts
  validate: (yaml_content: string) =>
    fetchApi<PipelineValidationResult>(`/api/v1/pipelines/validate`, {
      method: "POST",
      body: JSON.stringify({ yaml_content }),
    }),
```

- [ ] **Step 2: Enhance `extractErrorMessage` to format structured validation detail**

In `frontend/src/lib/api.ts`, inside `extractErrorMessage`, in the `if (... "detail" in body)` block, AFTER the `if (typeof detail === "string" ...)` line and BEFORE the `if (Array.isArray(detail))` line, insert:

```ts
    // Structured pipeline-validation detail: { message, errors: [{message, line}] }
    if (
      detail &&
      typeof detail === "object" &&
      !Array.isArray(detail) &&
      "errors" in detail
    ) {
      const errs = (detail as { errors: unknown }).errors;
      if (Array.isArray(errs) && errs.length) {
        const msgs = errs
          .map((e) => {
            if (e && typeof e === "object" && "message" in e) {
              const m = String((e as { message: unknown }).message);
              const ln = (e as { line?: unknown }).line;
              return typeof ln === "number" ? `Line ${ln}: ${m}` : m;
            }
            return null;
          })
          .filter((m): m is string => Boolean(m));
        if (msgs.length) return msgs.join("; ");
      }
    }
```

- [ ] **Step 3: Verify lint and build**

Run (from `frontend/`): `npm run lint && npm run build`
Expected: lint clean; build succeeds (TypeScript typecheck passes).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "$(cat <<'EOF'
feat(frontend): pipeline validate API client + format structured validation errors

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Surface trigger validation errors in the pipeline page

**Files:**
- Modify: `frontend/src/app/pipelines/[id]/page.tsx` (`handleTrigger` shows the API error message)

**Interfaces:**
- Consumes: `buildsApi.trigger` throwing an `ApiError` whose `.message` is the formatted validation string (from Task 9).
- Produces: a failed trigger shows the line-level validation message in a toast instead of a generic string.

> Verify with `npm run lint` and `npm run build` (no frontend test runner).

- [ ] **Step 1: Update `handleTrigger`**

In `frontend/src/app/pipelines/[id]/page.tsx`, replace the existing `handleTrigger`:

```ts
  async function handleTrigger() {
    try {
      const build = await buildsApi.trigger(id);
      toast.success("Build triggered!");
      router.push(`/builds/${build.id}`);
    } catch {
      toast.error("Failed to trigger build");
    }
  }
```

with:

```ts
  async function handleTrigger() {
    try {
      const build = await buildsApi.trigger(id);
      toast.success("Build triggered!");
      router.push(`/builds/${build.id}`);
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Failed to trigger build",
      );
    }
  }
```

(`fetchApi` throws an `ApiError` whose `message` is already the human-readable, line-numbered string produced by the enhanced `extractErrorMessage`.)

- [ ] **Step 2: Verify lint and build**

Run (from `frontend/`): `npm run lint && npm run build`
Expected: lint clean; build succeeds.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/app/pipelines/[id]/page.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): show line-level YAML validation error when a manual trigger is rejected

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: Editor live validation — problems list + in-editor underline

**Files:**
- Modify: `frontend/src/components/ui/yaml-editor.tsx` (add optional `diagnostics` prop; capture the `EditorView`; dispatch CodeMirror lint diagnostics)
- Modify: `frontend/src/components/pipeline/pipeline-editor.tsx` (debounced call to `pipelinesApi.validate`; render a problems list; pass diagnostics to `YamlEditor`)

**Interfaces:**
- Consumes: `pipelinesApi.validate`, `PipelineValidationError` (Task 9); `@codemirror/lint` (`setDiagnostics`, `Diagnostic`) — already installed.
- Produces:
  - `YamlEditor` accepts `diagnostics?: EditorDiagnostic[]` where `EditorDiagnostic = { line: number | null; column: number | null; message: string; severity?: "error" | "warning" }` (exported).
  - `PipelineEditor` shows a clickable problems list below the editor and underlines offending lines.

> Verify with `npm run lint` and `npm run build` (no frontend test runner).

- [ ] **Step 1: Add the `diagnostics` prop to `YamlEditor`**

In `frontend/src/components/ui/yaml-editor.tsx`:

Add the lint import near the other CodeMirror imports:

```ts
import { setDiagnostics, type Diagnostic } from "@codemirror/lint";
```

Export the diagnostic type and add the prop. Replace the `YamlEditorProps` interface with:

```ts
export interface EditorDiagnostic {
  line: number | null;
  column: number | null;
  message: string;
  severity?: "error" | "warning";
}

interface YamlEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  minHeight?: string;
  maxHeight?: string;
  className?: string;
  placeholder?: string;
  diagnostics?: EditorDiagnostic[];
}
```

Add `diagnostics` to the destructured props in the `YamlEditor({ ... })` signature.

Inside the component body, add a view ref and an effect that pushes diagnostics into CodeMirror (place after the existing `extensions` useMemo):

```ts
  const viewRef = React.useRef<EditorView | null>(null);

  React.useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const doc = view.state.doc;
    const diags: Diagnostic[] = (diagnostics ?? [])
      .filter((d) => d.line != null)
      .map((d) => {
        const lineNo = Math.min(Math.max(d.line ?? 1, 1), doc.lines);
        const lineObj = doc.line(lineNo);
        const from =
          d.column != null
            ? Math.min(lineObj.from + (d.column - 1), lineObj.to)
            : lineObj.from;
        return {
          from,
          to: lineObj.to,
          severity: d.severity ?? "error",
          message: d.message,
        } as Diagnostic;
      });
    view.dispatch(setDiagnostics(view.state, diags));
  }, [diagnostics]);
```

Wire the view ref into the `<CodeMirror ... />` element by adding the `onCreateEditor` prop:

```tsx
        onCreateEditor={(view) => {
          viewRef.current = view;
        }}
```

(`setDiagnostics` enables the lint extension automatically, so no extra extension wiring is needed.)

- [ ] **Step 2: Add debounced validation + problems list to `PipelineEditor`**

In `frontend/src/components/pipeline/pipeline-editor.tsx`:

Add imports at the top:

```ts
import { pipelinesApi, type PipelineValidationError } from "@/lib/api";
```

Inside the `PipelineEditor` component body (after the `callbacks` ref block, before the keydown `useEffect`), add validation state + a debounced effect:

```ts
  const [problems, setProblems] = React.useState<PipelineValidationError[]>([]);

  React.useEffect(() => {
    if (readOnly || !value.trim()) {
      setProblems([]);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const res = await pipelinesApi.validate(value);
        setProblems(res.errors);
      } catch {
        // A failing lint request must never block editing.
        setProblems([]);
      }
    }, 500);
    return () => clearTimeout(handle);
  }, [value, readOnly]);
```

Pass diagnostics to the `<YamlEditor .../>` (add the prop to the existing element):

```tsx
        diagnostics={problems.map((p) => ({
          line: p.line,
          column: p.column,
          message: p.message,
          severity: "error" as const,
        }))}
```

Add the problems list directly below the `<YamlEditor .../>` element, still inside the outer `<div className={cn("space-y-2", className)}>`:

```tsx
      {!readOnly && problems.length > 0 && (
        <ul className="space-y-1 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs">
          {problems.map((p, i) => (
            <li key={i} className="font-mono text-destructive">
              {p.line != null ? `Line ${p.line}: ` : ""}
              {p.message}
            </li>
          ))}
        </ul>
      )}
```

- [ ] **Step 3: Verify lint and build**

Run (from `frontend/`): `npm run lint && npm run build`
Expected: lint clean; build succeeds (typecheck passes — `EditorView` is already imported in `yaml-editor.tsx`; `Diagnostic`/`setDiagnostics` resolve from `@codemirror/lint`).

- [ ] **Step 4: Manual smoke check (optional but recommended)**

Run the frontend dev server, open a pipeline editor, type YAML with a bad indent, and confirm: a problems list appears within ~0.5s showing `Line N: ...`, and the offending line is underlined.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ui/yaml-editor.tsx frontend/src/components/pipeline/pipeline-editor.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): live YAML validation in the pipeline editor (problems list + line underline)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] Run the whole backend suite: `cd backend && pytest -q` — expected: all green.
- [ ] Frontend: `cd frontend && npm run lint && npm run build` — expected: clean lint, successful build.
- [ ] Manual end-to-end: save a pipeline with a bad-indent YAML; the editor shows `Line N: ...`; clicking "Run" shows the same line message in a toast and creates no build; a git webhook for the same pipeline produces a build marked `failed` whose logs show the `validation` / `yaml-check` error.
