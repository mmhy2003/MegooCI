# Single-Run Pipeline Concurrency — Design

**Date:** 2026-06-22
**Status:** Approved

## Problem

MegooCI runs builds with no per-pipeline concurrency control. Builds are
created `pending`, and [agent_dispatcher.py](../../../backend/app/services/agent_dispatcher.py)
hands each pending build to any free agent. If two builds for the same pipeline
are pending and two agents are free, **both run at once**. A webhook (or another
trigger) firing while a pipeline is already running starts a second concurrent
run.

The goal: **a pipeline must never run two builds at the same time.** When a
pipeline is already running and any trigger fires again, the new run is
**queued** (serialized) and executed after the current run finishes.

## Decision

Enforce two per-pipeline invariants, with the database as the source of truth:

- **≤1 `running` build per pipeline** — serialize: a pipeline runs one build at
  a time.
- **≤1 `pending` build per pipeline** — coalesce: at most one queued run; newer
  triggers replace the waiting run's target (latest wins).

Together, a pipeline holds at most **one `running` + one `pending`** build at
any moment. All other states (`success`/`failed`/`cancelled`) are unconstrained.

Scope, from the brainstorming decisions:

- **Concurrency key = `pipeline_id`** (branch-agnostic). "The same pipeline"
  means the same pipeline regardless of branch. Webhooks only ever build the
  pipeline's default branch, so this also matches practice.
- **All trigger sources** are subject to this: manual trigger, retry, git
  webhook, and the `trigger_pipeline` step. The guarantee holds no matter which
  path created the build.
- **Coalesce, latest wins:** a burst of triggers during a run collapses to a
  single queued run with the latest commit/branch/params. Intermediate triggers
  are absorbed, not stacked.
- **Global:** applies to every pipeline; no per-pipeline configuration.

### Why the database, not application code

Partial unique indexes make the invariants physically impossible to violate, so
the guarantee holds across all four creation paths and across concurrent Celery
workers / dispatch passes — with **no reservation state to leak or reconcile**
(unlike the agent-busy `current_build_id` pattern). The constraint is on the
builds' own `status`, which the existing finish/cancel logic already manages, so
it is self-healing as builds reach terminal states.

## Component 1 — Schema (migration `021`)

Two partial unique indexes on the `builds` table:

```python
# at most one running build per pipeline  → serialize
op.create_index(
    "uq_one_running_build_per_pipeline", "builds", ["pipeline_id"],
    unique=True, postgresql_where=sa.text("status = 'running'"),
)
# at most one pending build per pipeline  → coalesce
op.create_index(
    "uq_one_pending_build_per_pipeline", "builds", ["pipeline_id"],
    unique=True, postgresql_where=sa.text("status = 'pending'"),
)
```

- `down_revision = "020"` (current head). `downgrade()` drops both indexes.
- **Migration safety:** a unique index creation fails if duplicates already
  exist. Before creating each index, the migration reconciles pre-existing
  duplicates: for each pipeline with more than one `running` build, keep the
  most recent (by `created_at`) and mark the rest `cancelled`; for each pipeline
  with more than one `pending` build, keep the most recent and mark the rest
  `cancelled`. In a healthy system there are none — this only makes the
  migration safe to apply.

## Component 2 — Start gate (serialize the `pending → running` transition)

The single chokepoint where any build starts is `execute_build` /
`_run_build_stages` in [build_executor.py](../../../backend/app/services/build_executor.py),
so the guarantee lives there and holds for every path (direct `run_build.delay`
and the dispatcher alike). Two layers:

**Layer A — cheap pre-check (avoid needless agent churn).** In `execute_build`'s
first session block, right after the existing `status != "pending"` bail
(`build_executor.py:90`), add: if another build of this pipeline is currently
`running`, leave this build `pending`, release any pre-claimed agent (mirroring
the existing bail), close redis, and return.

```python
running_exists = await db.scalar(
    select(Build.id).where(
        Build.pipeline_id == build.pipeline_id,
        Build.status == "running",
        Build.id != build.id,
    ).limit(1)
)
if running_exists is not None:
    if claimed_agent_id is not None:
        try:
            await release_agent(db, claimed_agent_id, build_id)
        except Exception:
            pass
    await redis_client.aclose()
    return
```

**Layer B — atomic backstop (the real guarantee).** Layer A is TOCTOU-racy (two
builds of one pipeline can both pass it). The authoritative guard is the
`pending → running` flip in `_run_build_stages` (`build_executor.py:205`): set
`status="running"` and commit inside a `try`; on `IntegrityError` from
`uq_one_running_build_per_pipeline`, roll back, leave the build `pending`, and
return without executing. `execute_build`'s existing `finally` then releases the
agent and kicks `dispatch_pending_builds`, so the build is retried after the
pipeline frees.

**Re-dispatch path already exists:** when the running build finishes,
`execute_build`'s `finally` calls `dispatch_pending_builds`, which picks up the
waiting `pending` build — now unblocked. No new wakeup machinery is needed.

## Component 3 — Creation-time coalescing (≤1 pending, latest wins)

A shared helper all trigger paths funnel through:

```python
async def create_or_coalesce_build(
    db, pipeline, *, branch, commit_sha, params, triggered_by, trigger_type,
) -> tuple[Build, bool]:
    """Return (build, created). If the pipeline already has a pending build,
    coalesce the latest trigger's target into it; else create a new pending
    build."""
```

**Logic:**

1. Look for an existing `pending` build for the pipeline. If found →
   **coalesce**: overwrite `commit_sha`, `branch`, `params_json`, `trigger_type`,
   and `triggered_by` with the latest trigger's values; commit; return
   `(existing, created=False)`.
2. If none → **create** a new `pending` build; return `(build, created=True)`.
3. **Race-safe:** two triggers can both see "no pending" and both insert; the
   second hits `uq_one_pending_build_per_pipeline` → catch `IntegrityError`,
   roll back, re-query the now-existing pending, and coalesce into it. The index
   guarantees exactly one survives.

**Caller contract:** only when `created=True` does the path compile YAML →
stages/steps + `runs_on` and enqueue (`run_build.delay`). When `created=False`,
the run is already queued — do nothing further (no compile, no enqueue). This
replaces the bespoke create-then-compile-then-enqueue blocks in all four paths
([builds.py](../../../backend/app/api/v1/builds.py) manual + retry,
[webhooks_git.py](../../../backend/app/api/v1/webhooks_git.py),
[trigger.py](../../../backend/app/services/step_actions/trigger.py)).

**What coalescing does NOT do:** it does not recompile stages/steps. Those are
compiled from `pipeline.yaml_content` (DB), which is stable between rapid
triggers; the per-trigger thing that changes is the commit, which is refreshed.
If someone edits the pipeline YAML between two coalesced triggers, the queued
run uses the definition captured when the pending build was first created; the
next fresh run picks up the edit.

**Retry behavior (decided):** retry is treated like the other paths via the
helper. Retry-while-idle creates a pending by copying the original build's
frozen stages (as today). Retry-while-a-pending-exists coalesces its target into
the existing pending (latest-wins) — a retry can be absorbed into the queued run
rather than starting its own. The `created=True` branch is parameterized so
retry supplies "copy frozen stages" while the other paths supply "compile from
YAML".

## Component 4 — Dispatcher filter

The start gate guarantees correctness, but without help the dispatcher would
keep pre-claiming agents for builds that then bail at the running-transition.
`dispatch_pending_builds` and `dispatch_single_build`
([agent_dispatcher.py](../../../backend/app/services/agent_dispatcher.py)) skip
pending builds whose pipeline is busy:

- **Exclude pipelines with a running build.** When scanning the pending queue,
  skip any pending build whose `pipeline_id` already has a `running` build —
  expressed as a `NOT EXISTS` / correlated subquery against a `builds` self-alias
  filtered to `status='running'`.
- **One per pipeline per pass.** Within a single dispatch pass, track
  `pipeline_id`s already dispatched so two pendings of the same pipeline can't
  both be sent in one sweep. (With Component 1's index there is only ever ≤1
  pending per pipeline, so this is cheap belt-and-suspenders.)

This is purely an efficiency layer; the partial unique index + start gate remain
the correctness backstop, so a racing dispatch pass harmlessly leaves the second
build `pending`.

## Edge cases

- **Stuck `running` build** (worker died mid-build): blocks its pipeline
  indefinitely — no new run can start. This is the *existing* known gap (no
  running-build heartbeat/timeout). Remedy today: the operator cancels the stuck
  build, which frees the pipeline. A running-build timeout is a separate future
  fix, out of scope here.
- **Cancel/finish frees the pipeline:** a terminal running build is no longer
  counted by the index → the queued `pending` build becomes startable. The
  executor's `finally` already kicks `dispatch_pending_builds`, so the queued run
  starts promptly. A `pending` build cancelled before it ran simply frees the
  pending slot.
- **Coalesce vs. transition race:** coalescing only mutates `pending` builds.
  Once a build flips `pending → running` it is no longer eligible for
  coalescing, so a trigger arriving then creates a fresh pending — a clean
  handoff. A trigger landing in the exact transition instant is best-effort
  (it may build the commit just before or just after).
- **`trigger_pipeline` with `wait`:** if a child trigger coalesces into an
  existing pending, the parent step waits on that (coalesced) build — acceptable;
  it is the run that will happen for that pipeline.
- **Build numbers:** coalescing keeps the pending build's existing `number`;
  coalesced/skipped triggers do not mint new numbers.
- **Manual "Dispatch" / force paths:** still safe — the atomic start gate blocks
  a second concurrent run regardless of how the build was enqueued.

## Testing (TDD)

Backend pytest with the in-memory SQLite harness (the same `@compiles` shim
pattern as `tests/test_build_retry.py`).

- **Atomic guard:** the index-dependent tests create the two partial unique
  indexes on the SQLite test DB (SQLite supports `CREATE UNIQUE INDEX … WHERE …`),
  then assert: a second `pending → running` for the same pipeline is rejected
  (start gate leaves it pending); two concurrent `create_or_coalesce_build`
  calls yield exactly one pending (IntegrityError caught → coalesced).
- **Coalescing logic:** pipeline with a running build + three triggers → exactly
  one pending with the latest `commit_sha`.
- **Serialize end-to-end:** A running, B pending; A finishes → dispatch starts B
  (and not before).
- **Dispatcher filter:** a pending build for a busy pipeline is skipped; one for
  an idle pipeline dispatches.
- **Per-path:** webhook / manual / trigger_pipeline / retry while the pipeline is
  busy → coalesce, never a second running build.
- **Migration reconciliation:** pre-existing duplicate running/pending builds are
  collapsed to one each per pipeline before the indexes are created.

## Out of scope

- Running-build heartbeat/timeout to auto-heal builds stuck `running` (existing
  gap; documented remedy is operator cancel).
- Per-pipeline concurrency configuration (this is global).
- Per-branch concurrency groups (key is the whole pipeline).
- Cancel-in-progress-on-new-trigger ("supersede") behavior — the chosen policy
  is queue/serialize, not cancel-and-replace.
