# Builds List Mobile Details — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show all eight build fields on phones by rendering the builds list as a stacked card list below the `sm` breakpoint, while keeping the existing table unchanged at `sm` and up.

**Architecture:** A responsive dual render inside the builds list `CardContent`, both driven by the same `builds` array: the current `<table>` is hidden on mobile (`hidden sm:block`), and a new mobile card list (`sm:hidden`) maps each build through a small local `BuildCard` component that shows every field. No API, data, type, or state changes — purely presentational.

**Tech Stack:** Next.js 16 (App Router) · React 19 · TypeScript · Tailwind CSS v3 · lucide-react · date-fns. All work is in one file: `frontend/src/app/builds/page.tsx`.

---

## Testing Note (read first)

This frontend has **no test runner** configured — no jest/vitest/playwright config, no `test` script in `frontend/package.json`, and no project test files. We will **not** add a test framework for this presentational change (YAGNI). Verification for every task is:

1. **Type-check:** `npx tsc --noEmit` (run from `frontend/`)
2. **Lint:** `npm run lint` (run from `frontend/`)
3. **Manual visual check:** `npm run dev`, open `http://localhost:3000/builds`, and use the browser devtools device toolbar / window resize to confirm behaviour below and above 640px (`sm`).

If `npx tsc --noEmit` is unavailable for any reason, substitute `npm run build`, which also type-checks.

## File Structure

- **Modify:** `frontend/src/app/builds/page.tsx` — add a local `BuildCard` component; wrap the table for desktop-only; add the mobile card list; make the loading skeleton responsive.

No new files. The change stays in `page.tsx` because the new component is small, only used here, and shares the page's helpers (`statusVariant`, `formatDuration`) and resolved maps (`pipelineMap`, `projectMap`).

---

## Task 1: Add the `BuildCard` component and dual table/card render

**Files:**
- Modify: `frontend/src/app/builds/page.tsx`

All identifiers used by `BuildCard` (`useRouter`, `Build`, `Pipeline`, `Project`, `Badge`, `FolderKanban`, `statusVariant`, `formatDuration`, `formatDistanceToNow`) are **already imported** in this file — no import changes are needed.

- [ ] **Step 1: Add the `BuildCard` component**

Insert this component immediately **after** the `STATUS_TABS` constant (around line 56) and **before** `export default function BuildsPage()`. It mirrors the existing table row exactly: the outer element is a clickable `<div>` (matching the table's clickable `<tr>`), and the pipeline/project links are inner `<button>`s that call `stopPropagation` — the same pattern already used in the table.

```tsx
function BuildCard({
  build,
  pipeline,
  project,
}: {
  build: Build;
  pipeline?: Pipeline;
  project?: Project;
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
        <span>·</span>
        <span>{build.trigger_type}</span>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Wrap the table for desktop and add the mobile card list**

In `BuildsPage`, find the final `) : (` branch of the loading/empty/list conditional — the block that currently renders the table (around lines 195–294):

```tsx
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="w-full min-w-[560px] text-sm">
                  {/* …thead and tbody… */}
                </table>
              </div>
            )}
```

Replace **only** the outer wrapper and add the mobile list, wrapping both in a fragment. The `<table>` (its `<thead>`/`<tbody>` and everything inside) stays **byte-for-byte unchanged** — the only edit to the table is adding `hidden ... sm:block` to its wrapper `<div>`:

```tsx
            ) : (
              <>
                <div className="-mx-2 hidden overflow-x-auto px-2 sm:block">
                  <table className="w-full min-w-[560px] text-sm">
                    {/* …existing thead and tbody, UNCHANGED… */}
                  </table>
                </div>
                <div className="-mx-2 divide-y sm:hidden">
                  {builds.map((build) => {
                    const pl = pipelineMap[build.pipeline_id];
                    const prj = pl ? projectMap[pl.project_id] : undefined;
                    return (
                      <BuildCard
                        key={build.id}
                        build={build}
                        pipeline={pl}
                        project={prj}
                      />
                    );
                  })}
                </div>
              </>
            )}
```

Notes:
- The table wrapper gains `hidden` and `sm:block` (now: `-mx-2 hidden overflow-x-auto px-2 sm:block`).
- The mobile list uses `divide-y` for between-item separators (matching the table rows' `border-b`) and `-mx-2` so the per-card `px-2` hover background bleeds to the card edges.
- `pl` / `prj` are resolved the same way the table already resolves them.

- [ ] **Step 3: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors. (If `tsc` is unavailable, run `npm run build` instead and confirm it compiles.)

- [ ] **Step 4: Lint**

Run (from `frontend/`): `npm run lint`
Expected: no new errors or warnings for `src/app/builds/page.tsx`.

- [ ] **Step 5: Manual visual check**

Run (from `frontend/`): `npm run dev`, then open `http://localhost:3000/builds`.
- At **width < 640px** (devtools device toolbar): the list shows stacked cards. Each card shows `#number`, status badge, relative time, pipeline name, project (or `—`), and a branch · duration · trigger meta line — **all eight fields, no horizontal scroll**.
- At **width ≥ 640px**: the original table renders, unchanged.
- Tap a card → navigates to the build detail. Tap the pipeline name → navigates to the pipeline (card click does NOT also fire). Tap the project → navigates to the project.
- Switch the status-filter tabs → the card list filters too.
- Confirm a running build's duration counts up and a build with no project shows `—`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/builds/page.tsx
git commit -m "feat(builds): show full build details as cards on mobile"
```

---

## Task 2: Make the loading skeleton responsive

**Files:**
- Modify: `frontend/src/app/builds/page.tsx`

The current loading skeleton is a row of six fixed-width `Skeleton`s (`flex items-center gap-4`), which overflows a phone-width viewport. Show the existing row skeleton only at `sm`+ and a card-shaped skeleton below `sm`.

- [ ] **Step 1: Replace the loading skeleton block**

Find the loading branch (around lines 171–183):

```tsx
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-4">
                    <Skeleton className="h-4 w-16" />
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-4 w-20" />
                    <Skeleton className="h-4 w-20" />
                    <Skeleton className="h-5 w-16 rounded-md" />
                    <Skeleton className="ml-auto h-4 w-20" />
                  </div>
                ))}
              </div>
            ) : builds.length === 0 ? (
```

Replace the `{loading ? ( ... )` block (the opening `{loading ? (` and its `<div className="space-y-3">…</div>`) with:

```tsx
            {loading ? (
              <>
                {/* Desktop: row skeletons */}
                <div className="hidden space-y-3 sm:block">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4">
                      <Skeleton className="h-4 w-16" />
                      <Skeleton className="h-4 w-32" />
                      <Skeleton className="h-4 w-20" />
                      <Skeleton className="h-4 w-20" />
                      <Skeleton className="h-5 w-16 rounded-md" />
                      <Skeleton className="ml-auto h-4 w-20" />
                    </div>
                  ))}
                </div>
                {/* Mobile: card skeletons */}
                <div className="space-y-4 sm:hidden">
                  {Array.from({ length: 6 }).map((_, i) => (
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
            ) : builds.length === 0 ? (
```

- [ ] **Step 2: Type-check**

Run (from `frontend/`): `npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 3: Lint**

Run (from `frontend/`): `npm run lint`
Expected: no new errors or warnings.

- [ ] **Step 4: Manual visual check**

With `npm run dev` running, reload `/builds` (throttle network in devtools, or just observe the initial flash). At < 640px the loading state shows stacked card skeletons that do not overflow; at ≥ 640px the row skeletons are unchanged.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/builds/page.tsx
git commit -m "feat(builds): responsive loading skeleton for mobile build cards"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-06-04-builds-mobile-details-design.md`):
- Dual render (table hidden on mobile + card list) → Task 1, Step 2. ✓
- `BuildCard` props `build`/`pipeline?`/`project?` and behaviour (whole card → build; nested pipeline/project links with `stopPropagation`) → Task 1, Step 1. ✓
- Card layout: header (#num · status badge via `statusVariant` · relative time), pipeline line (primary link, short-id fallback), project line (muted, `FolderKanban`, `—` when none), meta line (branch `code` chip · `formatDuration` · trigger) → Task 1, Step 1. ✓
- Switch at `sm` (640px) → `hidden sm:block` / `sm:hidden` in Task 1, Step 2. ✓
- Responsive loading skeleton → Task 2. ✓
- Empty state and status tabs unchanged → untouched by both tasks. ✓
- No API/data/type/state changes → only `page.tsx` JSX + one local component. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to Task N". The one `{/* …existing thead and tbody, UNCHANGED… */}` marker is an explicit instruction to leave existing code untouched, not a placeholder to fill. ✓

**3. Type consistency:** Component name `BuildCard` and prop names `build`/`pipeline`/`project` are identical between the definition (Task 1, Step 1) and the call site (Task 1, Step 2). Field accesses (`build.id`, `build.number`, `build.status`, `build.branch`, `build.trigger_type`, `build.started_at`, `build.finished_at`, `build.created_at`, `build.pipeline_id`, `pipeline.name`, `project.id`, `project.name`) all match the usages already present in the current `page.tsx`. Helpers `statusVariant` / `formatDuration` are called with the same signatures as the table. ✓
