# Build cancellation actually terminates execution — design

**Date:** 2026-06-23
**Status:** Approved (pending spec review)

## Problem

Cancelling a running build does not stop work. The build is stamped
`cancelled`, but its stages and steps keep executing to completion. A
cancel today only takes real effect when the *whole* pipeline happens to
finish on its own.

### Root cause

In [`_run_build_stages`](../../../backend/app/services/build_executor.py) the
`build` ORM row is loaded **once** at the top of the function. The worker's
session uses `expire_on_commit=False`, and `try_start_build` sets
`build.status = "running"` in memory. The loop then guards each stage and
each step with `if build.status == "cancelled": break` — but that attribute
is **never re-read from the database** until *after* the loop finishes
(`await db.refresh(build)`). The cancel endpoint writes `"cancelled"` from a
**different** session, so the executor's in-memory copy stays `"running"`
forever and the guard never fires. The checks are dead code. The comment at
`builds.py` claiming "the local executor watches `build.status` between steps
and bails out on its own" describes behaviour that does not happen.

### Secondary gaps (also fixed here)

1. **Stage mis-finalization.** When the step loop breaks, the stage is
   stamped `"failed" if stage_failed else "success"`. A cancel would wrongly
   mark the stage `success`.
2. **Process-tree survival on agents.** The Go agent honours `cancel_step`
   (cancels the step's `context`, which SIGKILLs the `/bin/sh -c` /
   `cmd.exe /C` child), but `exec.CommandContext` kills only that *direct*
   child — a `docker build` / `npm` grandchild keeps running.
3. **Server-side gate hang.** `wait_input` (default timeout **24h**) and
   `wait_webhook` (default **1h**) run inside the executor, polling Redis in
   a loop. They never observe build status, so a build parked on a manual
   approval gate ignores a cancel until the gate resolves or times out.
4. **Dispatch→`step_started` race.** `notify_agents_of_cancel` only targets
   steps whose `agent_id` is already set. In the sub-second window after a
   step is dispatched but before the agent's `step_started` frame lands, a
   cancel misses the in-flight step.

## Goals

- A cancelled build stops launching further steps/stages within one step
  boundary.
- The in-flight step's **entire process tree** is killed on the agent.
- Builds parked on a `wait_input` / `wait_webhook` gate cancel within ≤2s.
- The build, its active stage/step, and everything not yet run all end
  `cancelled` (no half-green frozen pipeline in the UI).

## Non-goals

- Healing a build stuck `running` because its Celery worker died (separate
  heartbeat/timeout problem; tracked in the scheduler-dispatch notes).
- Any change to agent capacity or pipeline concurrency.

## Architecture — cooperative cancellation

Three execution surfaces can keep running; each gets the cheapest local
cancel check that fits it:

| Surface | Detection mechanism | Latency |
|---|---|---|
| Executor advancing to next step/stage | DB `status` refresh at each boundary | ≤1 step boundary |
| In-flight step on an agent | Push `cancel_step` frame → agent kills process tree | ~instant |
| In-flight server-side gate (`wait_*`) | Per-build Redis cancel flag, checked each 2s poll | ≤2s |

The DB `status="cancelled"` remains the single source of truth for the
build's final state. The Redis flag is a fast fan-out signal for the
long-lived gate loops (so they need not hold a DB transaction open for up to
24h).

## Component changes

### 1. One place to raise a cancel — `signal_build_cancel`

Add to [`agent_dispatcher.py`](../../../backend/app/services/agent_dispatcher.py):

```python
def build_cancel_flag_key(build_id) -> str:
    return f"build:{build_id}:cancel"

async def signal_build_cancel(db, build_id, redis_client=None) -> None:
    """Fan out a build cancellation: set the Redis cancel flag (so gate
    loops bail) and push cancel frames to any agent running its steps."""
    # set Redis flag build:{build_id}:cancel = "1" with a generous TTL
    #   (e.g. 90000s, longer than the longest gate timeout) so it self-cleans
    # then: await notify_agents_of_cancel(db, build_id)
```

A reader helper for the gate loops:

```python
async def build_cancel_requested(redis_client, build_id) -> bool:
    return await redis_client.get(build_cancel_flag_key(build_id)) is not None
```

`signal_build_cancel` is called from **every** path that sets a build to
`cancelled`:

- The [cancel endpoint](../../../backend/app/api/v1/builds.py) — replaces its
  current bare `notify_agents_of_cancel(db, build_id)` call (it can pass its
  existing `_redis` client).
- The pipeline cascade-delete path that already calls
  `notify_agents_of_cancel`.

> Loop-binding note: `signal_build_cancel` may be invoked from the API
> process (global `async_session`/loop) and must not import a foreign engine.
> It only needs the caller's `db` and a Redis client, both passed in.

### 2. Executor observes cancellation — `_run_build_stages`

In [`build_executor.py`](../../../backend/app/services/build_executor.py):

- **Boundary refresh.** Replace the two dead `if build.status == "cancelled"`
  checks (top of the stage loop and the step loop) with
  `await db.refresh(build, ["status"])` followed by the check. Refreshing a
  single column re-issues `SELECT status` and does **not** disturb the
  eager-loaded `stages`/`steps` collections. Track a local `cancelled` bool.
- **Cancelled step result is a stop.** After `_execute_step` returns, if
  `step_result.status == "cancelled"`, set `cancelled = True`, persist the
  step as `cancelled`, and break the inner loop. (Covers both the agent
  killing the in-flight step and a gate bailing.)
- **Cancel-aware stage finalization.** Change
  `stage.status = "failed" if stage_failed else "success"` to:
  `"cancelled" if cancelled else ("failed" if stage_failed else "success")`.
  Break the outer loop when `cancelled`.
- **Cancel-aware build finalization.** Keep the existing post-loop
  `await db.refresh(build)`; final status is `cancelled` when `cancelled`
  (or the refreshed status is `cancelled`). Additionally flip any stages and
  steps still in `pending`/`running` to `cancelled` so the UI shows a fully
  terminal pipeline.

Existing `build_started` / `build_finished` / per-step pub-sub events are
unchanged; the `build_finished` event already carries the final status.

### 3. Agent kills the whole process tree — `local.go`

In [`agent/internal/executor`](../../../agent/internal/executor/local.go), keep
the existing `cancel_step` → `context` cancellation plumbing
([`client.go`](../../../agent/internal/controller/client.go) `cancelStep`,
which already reports `StepFinished{Status: "cancelled"}`). Change only the
kill mechanics, using `cmd.Cancel` / `cmd.WaitDelay` (Go 1.22):

- **`process_unix.go`** (`//go:build !windows`):
  - `cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}` — shell + all
    descendants share one new process group.
  - `cmd.Cancel = func() error { return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL) }`
    — SIGKILL the whole group (note the negative PID).
  - `cmd.WaitDelay = 5 * time.Second` — a process ignoring the signal can't
    wedge `cmd.Wait()`.
- **`process_windows.go`** (`//go:build windows`):
  - Assign the child to a **Job Object** on start; terminate the job on
    cancel (kills the tree). Fallback if fiddly: `taskkill /T /F /PID <pid>`.

Introduce a small `configureProcessGroup(cmd)` helper, called wherever a
command is constructed — `buildCommand` and the `ai_agent` subprocess path —
so `Run` stays OS-agnostic.

### 4. Gate loops observe the cancel flag — `wait.py`

In [`WaitWebhookHandler.execute`](../../../backend/app/services/step_actions/wait.py)
and `WaitInputHandler.execute`, at the **top of each `while` iteration**
(both already hold a `redis_client`):

```python
if await build_cancel_requested(redis_client, ctx.build_id):
    yield LogLine(stream="system", content="Build cancelled.\n")
    yield StepResult(exit_code=1, status="cancelled")
    return
```

The `cancelled` StepResult flows back through `_run_handler` →
`_execute_step` → the executor loop, where rule (2)'s "cancelled step result
is a stop" halts the build.

### 5. Close the dispatch→`step_started` race — `notify_agents_of_cancel`

Broaden [`notify_agents_of_cancel`](../../../backend/app/services/agent_dispatcher.py)
so that, in addition to pushing `cancel_step` for every `running` step with an
`agent_id`, it also pushes a cancel to the agent **reserved for the build**
(`Agent.current_build_id == build_id`) for any step of that build it knows
about. This covers the window where a step was dispatched but its
`step_started` (which sets `agent_id`) has not yet landed. The executor's
boundary refresh (component 2) remains the ultimate backstop.

## Data flow — cancelling a running build

1. User hits `POST /builds/{id}/cancel`. Endpoint sets `status="cancelled"`,
   commits, indexes, calls `signal_build_cancel(db, id, redis)`.
2. `signal_build_cancel` sets `build:{id}:cancel` in Redis and pushes
   `cancel_step` frames to the agent(s) running this build's steps.
3. **In-flight agent step:** the agent's `cancelStep` cancels the step
   `context` → process group is SIGKILLed → agent sends
   `StepFinished{Status:"cancelled"}` → controller publishes to
   `step:{step_id}:result` → the executor's `dispatch_step_to_agent` unblocks
   and returns `cancelled`.
4. **In-flight gate step:** within ≤2s the poll loop sees the Redis flag and
   returns `StepResult(status="cancelled")`.
5. Executor (component 2): the returned `cancelled` step status — or the
   next-boundary `db.refresh(build, ["status"])` — halts the loop. The
   active stage/step are stamped `cancelled`; remaining stages/steps flipped
   to `cancelled`; build finalized `cancelled`; agent told `build_finished`
   for workspace cleanup (existing path).

## Testing

**Backend (pytest, async):**

- Executor stops on cancel: build status flips to `cancelled` between steps →
  no further steps dispatched; active + unrun stages/steps end `cancelled`;
  the active stage is **not** stamped `success`.
- A step returning `status="cancelled"` halts the loop and finalizes the
  build `cancelled`.
- `signal_build_cancel` sets the Redis flag and invokes the agent notifier.
- `wait_input` / `wait_webhook` return `status="cancelled"` promptly when the
  flag is set, instead of blocking to timeout.
- `notify_agents_of_cancel` targets the reserved agent even when a running
  step has no `agent_id` yet.

**Agent (Go, `local_test.go`):**

- Cancelling a step that spawns a child process kills the child too: start a
  command that backgrounds a long-lived grandchild, cancel the context,
  assert the grandchild is gone (Unix; Windows covered if Job Object path is
  used).

## Risks & mitigations

- **Redis flag outlives the build / stale flag.** Generous TTL self-cleans;
  the DB status is authoritative for final state, so a stale flag can at
  worst cause a brand-new build that reused an id (UUIDs — won't happen) to
  see a cancel. Negligible.
- **Boundary-only executor detection** means a single very long step is only
  interrupted via the agent push, not the DB refresh. That's by design — the
  push kills it ~instantly; the refresh is the backstop for between-steps.
- **Windows Job Object complexity.** `taskkill /T /F` fallback keeps the
  change shippable if the Job Object route stalls.
