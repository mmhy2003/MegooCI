# Pipeline cascade-delete with a two-step warning

**Date:** 2026-06-23
**Status:** Approved (design)

## Problem

Deleting a pipeline that has builds fails. The UI shows "Failed to delete
pipeline" and the pipeline survives.

The delete endpoint just calls `db.delete(pipeline)`
([`pipelines.py:156`](../../../backend/app/api/v1/pipelines.py)). Most of the
build subtree already cascades at the database level (migration `015`): builds
→ stages → steps → log_chunks → artifacts, plus triggers and webhook_endpoints.
Agent reservations (`agents.current_build_id`) and `container_images.build_id`
both `SET NULL`.

The one FK that does **not** cascade is `notification_deliveries.build_id` — a
plain `ForeignKey("builds.id")` with no `ON DELETE` rule
([`notification.py:65`](../../../backend/app/models/notification.py), migration
`008`). So when a pipeline has builds that ever produced a notification, the
database refuses to delete those builds, the cascade aborts with an integrity
error, and the whole delete fails.

The project delete already solves the equivalent problem: it offers a
cascade ("force") delete behind a clear warning, and it dodges the
`notification_deliveries` FK by deleting those rows explicitly
([`projects.py`](../../../backend/app/api/v1/projects.py)).

## Goal

Let a user delete a pipeline that has builds, via cascade delete behind a
warning, modeled on the existing project-delete flow:

1. A plain delete attempt that the backend refuses with `409` when dependents
   exist, returning a human-readable list of what's in the way.
2. A second "delete everything" confirmation that retries with `?force=true`
   and cascade-deletes the pipeline and its entire subtree.

## Decisions

These were settled during brainstorming:

- **UX: exact two-step mirror of the project flow.** Plain delete → backend
  `409` listing dependents → second confirm → retry with `?force=true`. Chosen
  over a single-step warning to stay consistent with the project flow.
- **Active builds: cancel then delete.** If the pipeline has a running/queued
  build, the force path cancels it (signalling agents) before cascading, rather
  than blocking or hard-deleting mid-run.
- **Cascade mechanism: schema migration (Approach A).** Add `ON DELETE CASCADE`
  to `notification_deliveries.build_id` so the database handles the full
  subtree. Chosen over a shared explicit-delete helper (B) or an inline copy
  (C) because it is the least code, fixes the root cause uniformly, and makes
  the project cascade simpler too.

## Design

### 1. Schema migration (root-cause fix)

New Alembic migration `022_cascade_delete_notification_deliveries`
(`down_revision = "021"` — the current head is `021_pipeline_run_concurrency`):

- `upgrade()`: drop constraint `notification_deliveries_build_id_fkey`, recreate
  it referencing `builds.id` with `ondelete="CASCADE"`.
- `downgrade()`: drop and recreate it as a plain FK (no `ondelete`).

Update the model to match: in
[`notification.py`](../../../backend/app/models/notification.py),
`NotificationDelivery.build_id` becomes
`ForeignKey("builds.id", ondelete="CASCADE")`.

After this, deleting a build (or a pipeline, which cascades to its builds)
cleanly removes the build's delivery rows. There is no standalone "delete
build" endpoint — builds are only ever deleted via pipeline or project cascade
— so cascading their deliveries is always the correct behavior.

**Consistency cleanup:** the project cascade's explicit
`sa_delete(NotificationDelivery)` becomes redundant once the FK cascades. Remove
that one statement from
[`projects.py`](../../../backend/app/api/v1/projects.py) so both delete paths
rely on the same database behavior. (Harmless to leave, but tidier to drop.)

### 2. Backend — pipeline `DELETE` endpoint

Rework `delete_pipeline`
([`pipelines.py:156`](../../../backend/app/api/v1/pipelines.py)) to mirror
`delete_project`. Permission is unchanged (`pipelines.manage`).

```
DELETE /api/v1/pipelines/{pipeline_id}?force=<bool>
```

Behavior:

1. Load the pipeline; `404` if it doesn't exist.
2. Count dependents:
   - builds (`Build.pipeline_id == pipeline_id`) — the headline,
   - triggers (`Trigger.pipeline_id == pipeline_id`),
   - webhook endpoints (`WebhookEndpoint.pipeline_id == pipeline_id`).
3. **Default (no force):** if any dependents exist, raise `409 Conflict` with a
   detail string beginning **"Cannot delete pipeline"**, e.g.:

   > Cannot delete pipeline: it has 42 build(s), 1 trigger(s), 2 webhook
   > endpoint(s). Retry with ?force=true to cascade-delete them.

   Only include the clauses whose count is non-zero. The leading phrase
   "Cannot delete pipeline" is the token the frontend matches on (mirroring the
   project's "Cannot delete project" convention).
4. **Force path** (`force=true`, dependents exist):
   1. Select builds with status in (`pending`, `queued`, `running`). For each,
      set `status = "cancelled"`, then **commit**, then signal the agent(s)
      running its steps to stop. Committing the cancellation first lets the
      local executor bail cleanly between steps — it already returns early when
      `build.status != "pending"`
      ([`build_executor.py:91`](../../../backend/app/services/build_executor.py)).
   2. `await db.delete(pipeline)` + commit. The database cascade removes the
      full subtree: builds → stages → steps → log_chunks → artifacts, plus
      triggers, webhook_endpoints, and (via the new migration) notification
      deliveries. Agent reservations clear via the existing `SET NULL`.
   3. `await remove_pipeline(str(pipeline_id))` from the search index, as today.
5. **No dependents:** delete succeeds with `204` whether or not `force` was
   passed (nothing to cascade).

**Shared agent-cancel helper.** The step-cancel signalling currently lives in
the module-private `_notify_agents_of_cancel`
([`builds.py:200`](../../../backend/app/api/v1/builds.py)). Lift it into a
shared location (e.g. a small function importable by both the builds and
pipelines routers, or a helper in the agent-dispatcher service layer) so the
pipeline force path and the existing single-build cancel use one implementation.
Keep its best-effort, error-swallowing behavior.

### 3. Frontend — two-step confirm

**API client** ([`api.ts`](../../../frontend/src/lib/api.ts)): change
`pipelinesApi.delete` to accept an options object, identical in shape to
`projectsApi.delete`:

```ts
delete: (id: string, opts?: { force?: boolean }) =>
  fetchApi<void>(
    `/api/v1/pipelines/${id}${opts?.force ? "?force=true" : ""}`,
    { method: "DELETE" },
  ),
```

**Pipeline detail page** (`handleDelete` in
[`pipelines/[id]/page.tsx`](../../../frontend/src/app/pipelines/[id]/page.tsx)):
restructure to mirror `handleDeleteProject`:

1. First confirm — neutral *"Delete this pipeline?"* (the cascade warning moves
   to the second dialog, matching the project flow).
2. Attempt `pipelinesApi.delete(id)`. On success → success toast → redirect to
   `/pipelines`.
3. On error, read `err.body.detail`. If it contains the (case-insensitive)
   phrase *"cannot delete pipeline"*, open a second confirm
   *"Delete pipeline and all its builds?"* that shows the detail plus a clear
   warning that this permanently removes **all builds, logs, and artifacts**,
   then retry with `pipelinesApi.delete(id, { force: true })`.
4. Any other error (403/404/500) → surface `detail` (or a fallback) as a toast.

The pipeline detail page is the only delete entry point — the pipelines list
page has no delete button — so no other UI changes are needed.

## Testing

Backend (pytest, async):

- Delete a pipeline that has builds **without** `force` → `409`; pipeline and
  builds still present.
- Delete with `force=true` → pipeline and the full subtree are gone: builds,
  stages, steps, log_chunks, artifacts, triggers, webhook_endpoints, and
  notification_deliveries.
- Delete with `force=true` when a build is `running` → the build is
  cancelled/removed and the agent's `current_build_id` is cleared (`SET NULL`).
- Delete a pipeline with **no** builds → `204` without `force`.
- Delete a missing pipeline → `404`.
- Migration-level: deleting a build cascades its `notification_deliveries` rows.

Frontend: no existing delete-flow component tests to mirror; rely on the backend
suite plus manual verification of the two-step dialog (plain delete →
`409` → "delete everything" → force succeeds).

## Out of scope

- Bulk pipeline deletion.
- A standalone "delete build" endpoint.
- Cleaning up cascade-deleted builds/artifacts from the search index
  (pre-existing gap shared with the project cascade; not introduced here).
- Re-parenting or archiving build history instead of deleting it.
