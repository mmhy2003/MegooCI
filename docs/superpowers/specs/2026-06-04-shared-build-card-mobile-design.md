# Shared mobile build card — Builds page + Dashboard

**Date:** 2026-06-04
**Status:** Approved (design)
**Area:** `frontend/src/app/builds/page.tsx`, `frontend/src/app/dashboard/page.tsx`, plus two new shared modules

## Problem

The mobile stacked-card render of the builds list (shipped previously) shows full
build details on phones where the desktop table hides columns. The dashboard's
**Recent Builds** card uses the same table pattern with the same progressive column
hiding, so on a phone it also collapses to just Build #, Status, and Time.

We want the dashboard's recent-builds list to get the same mobile-card treatment.
Because the card now needs to exist in two places, the card should become a single
shared component rather than being copy-pasted.

## Goal

- On the dashboard, show recent builds as stacked cards below `sm` (640px), with the
  same fields the dashboard's desktop table shows; keep the table at `sm`+.
- Extract the mobile build card into one shared component used by both the builds
  page and the dashboard, so the two never drift.
- Remove the duplicated `statusVariant` / `formatDuration` helpers (currently
  identical in both page files) into one shared module.

## Non-goals

- No changes to data fetching, the live-update (WebSocket) logic, the dashboard's
  10-item cap, stat cards, empty states, or the "View all" header.
- No changes to the desktop tables themselves beyond wrapping them so they hide on
  mobile.
- No visual change to the builds page's existing mobile card (the extracted component
  must render identically to today's inline one when trigger is shown).
- No new npm dependencies.

## Field parity

The dashboard's desktop recent-builds table shows **7 fields**: Build #, Pipeline,
Project, Branch, Status, Duration, Time — it has **no Trigger column** (unlike the
builds page, which has 8 including Trigger). The dashboard mobile card therefore omits
the trigger; the builds page mobile card keeps it. This is controlled by a
`showTrigger` prop.

## Approach

### New: `frontend/src/lib/builds.ts`

Single home for the two helpers currently duplicated in both page files (the
implementations are identical):

```ts
import { type BuildStatus } from "@/lib/api";

export function statusVariant(
  s: BuildStatus,
): "success" | "failed" | "running" | "pending" | "cancelled" {
  const map: Record<
    BuildStatus,
    "success" | "failed" | "running" | "pending" | "cancelled"
  > = {
    pending: "pending",
    queued: "pending",
    running: "running",
    success: "success",
    failed: "failed",
    cancelled: "cancelled",
  };
  return map[s];
}

export function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const secs = Math.floor((e - s) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ${secs % 60}s`;
}
```

### New: `frontend/src/components/builds/build-card.tsx`

The mobile card, lifted verbatim from the builds page's current inline `BuildCard`,
with one addition: a `showTrigger` prop (default `true`) that gates the trigger token
on the meta line.

**Props:** `{ build: Build; pipeline?: Pipeline; project?: Project; showTrigger?: boolean }`

**Behaviour:** unchanged from the current inline card — whole card → `/builds/{id}`;
nested pipeline/project links call `stopPropagation`; `—` fallbacks for missing
project/branch. Imports `statusVariant`/`formatDuration` from `@/lib/builds`,
`Badge`, `FolderKanban`, `formatDistanceToNow`, `useRouter`, and the `Build`/
`Pipeline`/`Project` types. The meta line renders the branch chip and duration always,
and `· {build.trigger_type}` only when `showTrigger` is true (default).

### Modified: `frontend/src/app/builds/page.tsx`

- Delete the local `BuildCard` component and the local `statusVariant` /
  `formatDuration` functions.
- Import `BuildCard` from `@/components/builds/build-card` and `statusVariant` /
  `formatDuration` from `@/lib/builds`.
- The mobile card list now renders `<BuildCard key=… build=… pipeline=… project=… />`
  (trigger shown by default → identical to current behaviour). The desktop table and
  everything else are unchanged.

### Modified: `frontend/src/app/dashboard/page.tsx`

- Delete the local `statusVariant` / `formatDuration` functions; import them from
  `@/lib/builds`. Import `BuildCard` from `@/components/builds/build-card`.
- Wrap the recent-builds table's wrapper `<div>` so it is desktop-only:
  `-mx-2 hidden overflow-x-auto px-2 sm:block` (currently `-mx-2 overflow-x-auto px-2`).
- After the table wrapper, add a mobile card list inside the same fragment:
  ```tsx
  <div className="-mx-2 divide-y sm:hidden">
    {recentBuilds.map((build) => {
      const pl = pipelineMap[build.pipeline_id];
      const prj = pl ? projectMap[pl.project_id] : undefined;
      return (
        <BuildCard
          key={build.id}
          build={build}
          pipeline={pl}
          project={prj}
          showTrigger={false}
        />
      );
    })}
  </div>
  ```
- Make the loading skeleton responsive: keep the existing 5-row skeleton as
  `hidden sm:block`, and add an `sm:hidden` block of card-shaped skeletons (same shape
  as the builds page's mobile skeleton).

## Data flow

Unchanged. Both pages already compute `recentBuilds`/`builds`, `pipelineMap`,
`projectMap`, and receive live updates. The card lists read the same already-computed
values. The dashboard's prepend + `.slice(0, 10)` cap and stat recalculation are
untouched.

## Edge cases

- No pipeline → short-id fallback; no project → `—`; no branch → `—`; running build →
  duration counts up. All identical to the existing card.
- Dashboard card with `showTrigger={false}` simply omits the trailing `· trigger`
  token; the rest of the meta line is unchanged.

## Testing / verification

This frontend has no test runner (no jest/vitest/playwright, no `test` script); we are
not adding one for a presentational refactor. Verification:

- Type-check: `npx tsc --noEmit` (from `frontend/`) — clean.
- Manual: `npm run dev`, open `/dashboard` and `/builds`.
  - Dashboard < 640px: recent builds show as cards with Build #, status, time,
    pipeline, project (or `—`), branch · duration — **no trigger**, no horizontal
    scroll. ≥ 640px: table unchanged.
  - Builds page < 640px: cards still show all 8 fields **including trigger**
    (no regression). ≥ 640px: table unchanged.
  - On both, tapping a card opens the build; pipeline/project links navigate
    separately; status changes and new builds still appear live.
- Note: `npm run lint` is broken project-wide (no ESLint config) — pre-existing,
  out of scope.

## Out of scope / future

The artifacts and registry list pages use the same table-with-hidden-columns pattern;
giving them mobile cards could reuse this shared component later, but is not part of
this change.
