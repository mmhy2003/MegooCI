# Builds list — full build details on mobile

**Date:** 2026-06-04
**Status:** Approved (design)
**Area:** `frontend/src/app/builds/page.tsx`

## Problem

The builds list renders as a single `<table>` whose lower-priority columns are
hidden at smaller breakpoints. On a phone (below the `sm` / 640px breakpoint) only
three of the eight fields survive — **Build #**, **Status**, and relative **Time**.
The Pipeline, Project, Branch, Duration, and Trigger columns are all hidden via
`hidden sm:table-cell` / `md:` / `lg:` / `xl:` classes, and because the columns are
removed (not merely scrolled off), horizontal scrolling does not reveal them.

Users want the mobile list to show the **same details that the desktop view shows** —
all eight fields.

## Goal

On mobile, surface every field the desktop table shows, in a layout that is
readable on a narrow screen without horizontal scrolling.

Desktop behaviour is unchanged.

## Non-goals

- No changes to the API, data fetching, types, or live-update (WebSocket) logic.
- No changes to the status-filter tabs, header, or empty state.
- No redesign of the desktop table or its progressive column hiding.
- No changes to any other list page (artifacts, registry, etc.).

## Approach

Use a **responsive dual render** inside the builds list `CardContent`, both driven by
the same `builds` array:

1. **Desktop:** the existing `<table>`, wrapped so it is hidden on mobile
   (`hidden sm:block`). Markup and column behaviour are unchanged.
2. **Mobile:** a new vertical card list, visible only below `sm`
   (`space-y-3 sm:hidden`). Each build renders as one stacked card showing all
   eight fields.

The switch point is the **`sm` (640px)** breakpoint — phones get cards; tablets and
wider get the table. This matches the boundary the page already uses for its header,
subtitle, and filter tabs. (Considered and rejected: switching at `lg`/`md`. `sm`
keeps the page's breakpoint model consistent; the table's own progressive disclosure
covers intermediate widths.)

To avoid duplicating the pipeline/project lookup and the per-row JSX across the two
renders, extract a small local `BuildCard` component within `page.tsx`.

## Components

### `BuildCard` (new, local to `page.tsx`)

**Props:**
- `build: Build`
- `pipeline?: Pipeline` — the resolved pipeline for `build.pipeline_id`
- `project?: Project` — the resolved project for `pipeline.project_id`

**Behaviour:** mirrors the existing table row.
- The whole card is a clickable surface → `router.push(/builds/{build.id})`
  (`cursor-pointer`, hover background, same as the current `<tr>`).
- Nested pipeline and project links call `e.stopPropagation()` so they navigate to
  their own destinations without also triggering the card click.

**Layout (top to bottom):**
1. **Header row** (`flex items-center gap-2`):
   - `#{build.number}` — bold.
   - Status `<Badge variant={statusVariant(build.status)}>` — reuses the existing
     helper, same variants as desktop.
   - Relative time — `formatDistanceToNow(build.created_at, { addSuffix: true })`,
     muted, pushed right (`ml-auto`).
2. **Pipeline line** — primary-colored link → `/pipelines/{build.pipeline_id}`,
   text `pipeline?.name || build.pipeline_id.slice(0, 8) + "…"` (same fallback as
   the table).
3. **Project line** — muted, `FolderKanban` icon, link → `/projects/{project.id}`
   showing `project.name`; renders `—` when there is no resolved project (matching
   the table's `—`).
4. **Meta line** (`flex flex-wrap gap-x` of muted text):
   - Branch as a `<code>` chip — `build.branch || "—"`.
   - Duration — `formatDuration(build.started_at, build.finished_at)`.
   - Trigger — `build.trigger_type`.

Existing module-level helpers (`statusVariant`, `formatDuration`) and imports
(`FolderKanban`, `Badge`, `formatDistanceToNow`) are reused; no new dependencies.

### Builds list `CardContent` (modified)

Replaces the single table block with:

```
{loading ? <ResponsiveSkeleton/>
 : builds.length === 0 ? <EmptyState/>   // unchanged
 : <>
     <div className="hidden sm:block"> …existing table… </div>
     <div className="space-y-3 sm:hidden">
       {builds.map((b) => <BuildCard key={b.id} build={b}
          pipeline={pipelineMap[b.pipeline_id]}
          project={pipelineMap[b.pipeline_id] ? projectMap[pipelineMap[b.pipeline_id].project_id] : undefined} />)}
     </div>
   </>}
```

### Loading skeleton (modified)

Make the skeleton responsive so it does not overflow on a phone: the existing
row-style skeleton stays for `sm` and up (`hidden sm:…`), with a simpler
card-shaped skeleton block shown below `sm`. Cosmetic only.

## Data flow

Unchanged. `BuildsPage` still loads builds/pipelines/projects, builds the
`pipelineMap` and `projectMap`, applies the status filter, and receives live updates
via `useBuildUpdates`. The card list reads from the same already-computed `builds`,
`pipelineMap`, and `projectMap` — no new fetching or state.

## Edge cases

- **No pipeline match:** pipeline link falls back to the shortened id (as today).
- **No project:** project line shows `—`.
- **No branch:** branch chip shows `—`.
- **Running build (no `finished_at`):** `formatDuration` already counts up from
  `started_at` to now; the card inherits that behaviour.
- **Live updates:** new builds prepend and status changes mutate in place exactly as
  now, since both renders read the same array.

## Testing / verification

- Manual: view `/builds` at <640px (cards, all 8 fields visible, no horizontal
  scroll) and ≥640px (unchanged table).
- Tap a card → build detail; tap pipeline link → pipeline; tap project link →
  project (card click does not also fire).
- Status filter tabs still filter the card list.
- A running build shows a live-counting duration; a new build appears at the top.
- Degraded data (no project / no branch) shows `—` without layout breakage.

## Out of scope / future

Applying the same mobile-card treatment to other table-based list pages (artifacts,
registry) could be a follow-up if this pattern proves out, but is not part of this
change.
