# Shared Mobile Build Card (Builds + Dashboard) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the dashboard's Recent Builds list the same mobile stacked-card treatment as the builds page, by extracting one shared `BuildCard` component (and the shared `statusVariant`/`formatDuration` helpers) and using it in both places.

**Architecture:** Two new shared modules — `lib/builds.ts` (helpers) and `components/builds/build-card.tsx` (the card, with a `showTrigger` prop). The builds page is refactored to consume them (no behaviour change). The dashboard adds a `hidden sm:block` table / `sm:hidden` card-list split plus a responsive skeleton, rendering the shared card with `showTrigger={false}` to match its 7-field desktop table.

**Tech Stack:** Next.js 16 (App Router) · React 19 · TypeScript · Tailwind CSS v3 · lucide-react · date-fns.

---

## Testing Note (read first)

This frontend has **no test runner** (no jest/vitest/playwright, no `test` script). We will **not** add one for this presentational refactor (YAGNI). Verification for every task:

1. **Type-check:** from `frontend/`, run `npx tsc --noEmit` — expect no errors. (Fallback if `tsc` unavailable: `npm run build`.)
2. **Manual visual check** (final task / as noted): `npm run dev`, open `/dashboard` and `/builds`, resize across the 640px (`sm`) breakpoint.

`npm run lint` is **known-broken project-wide** (the frontend has no ESLint config); that is pre-existing and out of scope — do not try to fix it.

Run commands from the frontend dir, e.g. (Bash) `cd /c/Projects/MegooCI/frontend && npx tsc --noEmit`. You are on Windows; use the Bash or PowerShell tool and report actual output.

## File Structure

- **Create:** `frontend/src/lib/builds.ts` — `statusVariant` + `formatDuration` (single source; currently duplicated in both pages).
- **Create:** `frontend/src/components/builds/build-card.tsx` — shared mobile `BuildCard` with a `showTrigger` prop.
- **Modify:** `frontend/src/app/builds/page.tsx` — delete the local `BuildCard` and the two local helpers; import the shared versions.
- **Modify:** `frontend/src/app/dashboard/page.tsx` — delete the two local helpers; import shared helpers + `BuildCard`; add the desktop/mobile split and a responsive skeleton.

Tasks are ordered so the codebase compiles after every commit.

---

## Task 1: Create the shared `lib/builds.ts` and `components/builds/build-card.tsx`

**Files:**
- Create: `frontend/src/lib/builds.ts`
- Create: `frontend/src/components/builds/build-card.tsx`

These new modules are not yet consumed by any page after this task — that is expected; they will compile cleanly (TypeScript does not error on unused exports).

- [ ] **Step 1: Create `frontend/src/lib/builds.ts`**

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

- [ ] **Step 2: Create `frontend/src/components/builds/build-card.tsx`**

This is the builds page's current inline `BuildCard`, moved into its own file, with imports added and a `showTrigger` prop (default `true`) gating the trigger token on the meta line. When `showTrigger` is true (the default) the output is identical to today's inline card.

```tsx
"use client";

import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { FolderKanban } from "lucide-react";
import { type Build, type Pipeline, type Project } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { statusVariant, formatDuration } from "@/lib/builds";

export function BuildCard({
  build,
  pipeline,
  project,
  showTrigger = true,
}: {
  build: Build;
  pipeline?: Pipeline;
  project?: Project;
  showTrigger?: boolean;
}) {
  const router = useRouter();
  return (
    <div
      onClick={() => router.push(`/builds/${build.id}`)}
      className="cursor-pointer px-2 py-3 transition-colors hover:bg-muted/50"
    >
      {/* Header: number · status · time */}
      <div className="flex items-center gap-2">
        <span className="font-medium">#{build.number}</span>
        <Badge variant={statusVariant(build.status)}>{build.status}</Badge>
        <span className="ml-auto text-xs text-muted-foreground">
          {formatDistanceToNow(new Date(build.created_at), { addSuffix: true })}
        </span>
      </div>

      {/* Pipeline */}
      <div className="mt-2">
        <button
          className="text-sm text-primary hover:underline"
          onClick={(e) => {
            e.stopPropagation();
            router.push(`/pipelines/${build.pipeline_id}`);
          }}
        >
          {pipeline?.name || build.pipeline_id.slice(0, 8) + "…"}
        </button>
      </div>

      {/* Project */}
      <div className="mt-1.5 text-sm">
        {project ? (
          <button
            className="flex items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              router.push(`/projects/${project.id}`);
            }}
          >
            <FolderKanban className="h-3.5 w-3.5" />
            {project.name}
          </button>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </div>

      {/* Meta: branch · duration · trigger */}
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <code className="rounded bg-muted px-1.5 py-0.5">
          {build.branch || "—"}
        </code>
        <span>·</span>
        <span>{formatDuration(build.started_at, build.finished_at)}</span>
        {showTrigger && (
          <>
            <span>·</span>
            <span>{build.trigger_type}</span>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/builds.ts frontend/src/components/builds/build-card.tsx
git commit -m "feat(builds): extract shared BuildCard component and build helpers"
```

---

## Task 2: Refactor the builds page to use the shared modules

**Files:**
- Modify: `frontend/src/app/builds/page.tsx`

Goal: remove the now-duplicated inline `BuildCard` and the two local helpers, import the shared versions instead. No behaviour or visual change (the mobile card still shows the trigger because `showTrigger` defaults to `true`).

- [ ] **Step 1: Add the two shared imports**

In the import block at the top of `frontend/src/app/builds/page.tsx`, after the existing `useBuildUpdates` import line (`import { useBuildUpdates } from "@/hooks/use-build-updates";`), add:

```tsx
import { BuildCard } from "@/components/builds/build-card";
import { statusVariant, formatDuration } from "@/lib/builds";
```

- [ ] **Step 2: Delete the two local helper functions**

Delete this block (the `statusVariant` and `formatDuration` function definitions near the top of the file — they are now imported):

```tsx
function statusVariant(
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

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const secs = Math.floor((e - s) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ${secs % 60}s`;
}
```

- [ ] **Step 3: Delete the local `BuildCard` component**

Delete the entire local `BuildCard` function (it currently sits between the `STATUS_TABS` constant and `export default function BuildsPage()`). It starts with `function BuildCard({` and ends with the matching `}` of that function. The call site `<BuildCard key={build.id} build={build} pipeline={pl} project={prj} />` inside `BuildsPage` stays exactly as-is — it now resolves to the imported component.

- [ ] **Step 4: Verify no orphaned imports**

Confirm the remaining code still uses `Badge`, `FolderKanban`, `formatDistanceToNow`, and the `Build`/`Pipeline`/`Project`/`BuildStatus` types (the desktop table still does). They must remain imported. `BuildStatus` is still referenced by `statusFilter` state and `STATUS_TABS`; `Badge`/`FolderKanban`/`formatDistanceToNow` are still used by the table rows. Do **not** remove those imports.

- [ ] **Step 5: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors. (If it reports an unused variable, you removed something still in use, or left a dead import — fix accordingly.)

- [ ] **Step 6: Manual visual check (builds page)**

With `npm run dev`, open `/builds`. At < 640px the mobile cards must look **exactly** as before — all 8 fields including the trailing `· {trigger}` on the meta line. At ≥ 640px the table is unchanged.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/builds/page.tsx
git commit -m "refactor(builds): use shared BuildCard and build helpers"
```

---

## Task 3: Add mobile cards + responsive skeleton to the dashboard Recent Builds

**Files:**
- Modify: `frontend/src/app/dashboard/page.tsx`

- [ ] **Step 1: Swap local helpers for shared imports**

In `frontend/src/app/dashboard/page.tsx`, after the existing `useBuildUpdates` import line, add:

```tsx
import { BuildCard } from "@/components/builds/build-card";
import { statusVariant, formatDuration } from "@/lib/builds";
```

Then delete the local `statusVariant` and `formatDuration` function definitions (identical block to the one removed from the builds page):

```tsx
function statusVariant(
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

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const secs = Math.floor((e - s) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}
```

(`BuildStatus` is still imported and used by `calcStats`/the WS callback; keep it. `FolderKanban`, `Badge`, `formatDistanceToNow` are still used by the desktop table; keep them.)

- [ ] **Step 2: Make the loading skeleton responsive**

Find the Recent Builds loading branch:

```tsx
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-4">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-4 w-16" />
                    <Skeleton className="h-4 w-20" />
                    <Skeleton className="h-5 w-16 rounded-md" />
                    <Skeleton className="ml-auto h-4 w-24" />
                  </div>
                ))}
              </div>
            ) : !recentBuilds?.length ? (
```

Replace the `<div className="space-y-3">…</div>` with:

```tsx
              <>
                {/* Desktop: row skeletons */}
                <div className="hidden space-y-3 sm:block">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4">
                      <Skeleton className="h-4 w-32" />
                      <Skeleton className="h-4 w-24" />
                      <Skeleton className="h-4 w-16" />
                      <Skeleton className="h-4 w-20" />
                      <Skeleton className="h-5 w-16 rounded-md" />
                      <Skeleton className="ml-auto h-4 w-24" />
                    </div>
                  ))}
                </div>
                {/* Mobile: card skeletons */}
                <div className="space-y-4 sm:hidden">
                  {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="space-y-2">
                      <div className="flex items-center gap-2">
                        <Skeleton className="h-4 w-12" />
                        <Skeleton className="h-5 w-16 rounded-md" />
                        <Skeleton className="ml-auto h-3 w-16" />
                      </div>
                      <Skeleton className="h-4 w-32" />
                      <Skeleton className="h-4 w-24" />
                      <Skeleton className="h-3 w-40" />
                    </div>
                  ))}
                </div>
              </>
```

So the branch becomes `{loading ? ( <>…</> ) : !recentBuilds?.length ? (`.

- [ ] **Step 3: Split the list into desktop table + mobile cards**

Find the final `) : (` branch that renders the table:

```tsx
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="w-full min-w-[540px] text-sm">
                  {/* …thead and tbody… */}
                </table>
              </div>
            )}
```

Replace **only** the wrapper `<div>` and add the mobile list, wrapping both in a fragment. The `<table>` and its `<thead>`/`<tbody>` stay **byte-for-byte unchanged** — only its wrapper div gains `hidden ... sm:block`:

```tsx
            ) : (
              <>
                <div className="-mx-2 hidden overflow-x-auto px-2 sm:block">
                  <table className="w-full min-w-[540px] text-sm">
                    {/* …existing thead and tbody, UNCHANGED… */}
                  </table>
                </div>
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
              </>
            )}
```

(`recentBuilds` is already narrowed to non-null in this branch — the existing table maps it the same way.)

- [ ] **Step 4: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Manual visual check (dashboard)**

With `npm run dev`, open `/dashboard`.
- < 640px: Recent Builds shows stacked cards with Build #, status, time, pipeline, project (or `—`), branch · duration — **and NO trigger** — with no horizontal scroll. Loading state shows card skeletons that don't overflow.
- ≥ 640px: the table is unchanged.
- Tapping a card opens the build; pipeline/project links navigate separately; new builds still appear at the top (capped at 10) and status changes update live.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/dashboard/page.tsx
git commit -m "feat(dashboard): show recent builds as cards on mobile"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-04-shared-build-card-mobile-design.md`):
- New `lib/builds.ts` with shared `statusVariant`/`formatDuration` → Task 1, Step 1. ✓
- New shared `BuildCard` with `showTrigger` prop (default true) → Task 1, Step 2. ✓
- Builds page consumes shared modules, no behaviour change (trigger still shown) → Task 2. ✓
- Dashboard: helpers imported, table `hidden sm:block`, `sm:hidden` mobile list with `showTrigger={false}`, responsive skeleton → Task 3. ✓
- Switch at `sm`; data/live-update/10-item-cap/stat-cards/empty-state untouched → Tasks 2–3 only touch the listed JSX. ✓
- No new dependencies → only new local modules + imports. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases". The `{/* …existing thead and tbody, UNCHANGED… */}` markers are explicit "leave unchanged" instructions, not placeholders. ✓

**3. Type consistency:** The shared `BuildCard` prop names (`build`, `pipeline`, `project`, `showTrigger`) match both call sites — builds page (Task 2, uses default `showTrigger`) and dashboard (Task 3, `showTrigger={false}`). The exported helper names `statusVariant`/`formatDuration` match the imports added in Tasks 2 and 3. Field accesses inside `BuildCard` are unchanged from the already-shipped inline version. The `formatDuration` body placed in `lib/builds.ts` (Task 1) returns `` `${mins}m ${secs % 60}s` `` — behaviourally identical to the dashboard's `const rem = secs % 60; ${mins}m ${rem}s` removed in Task 3. ✓
