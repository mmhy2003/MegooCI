# Agent "busy" status on the agents page card

**Date:** 2026-06-04
**Status:** Approved design

## Goal

When an agent is executing a build, its card on the Agents page should show a
"busy" status and a clickable link to the build it is running
(`Building <pipeline> #<number>`), instead of plainly showing "online".

## Problem / context

The frontend is already partly wired for this, but the backend never produces a
"busy" state:

- The agents page already maps `status === "busy"` to the amber "running" badge
  variant (`frontend/src/app/agents/page.tsx`, `agentStatusVariant`) and already
  counts `statusCounts["busy"]` in the header. The `AgentStatus` type in
  `frontend/src/lib/api.ts` already includes `"busy"`.
- The backend `Agent.status` column is **only ever** `"online"` or `"offline"`.
  It tracks connectivity, set by the heartbeat endpoint, the agent WebSocket
  connect/disconnect handlers, and `_normalize_status` (stale → offline).
- Busyness is tracked in a **separate** field, `Agent.current_build_id`
  (NULL = idle), which drives dispatch (`pick_online_agent` /
  `claim_agent` / `release_agent`) but is **never exposed** in
  `AgentResponse` (`backend/app/schemas/agent.py`).

So the card cannot show "busy" today: the two concepts live in different fields
and the busy one is not sent to the UI.

## Chosen approach: derive "busy" in the API response layer

The stored facts stay clean and unduplicated:

- The DB `status` column remains raw connectivity (`online` / `offline`), which
  `pick_online_agent` depends on — untouched.
- Busyness remains authoritatively tracked by `current_build_id` — untouched.
- The human-facing "busy" state is **computed at read time** when serializing
  `AgentResponse`. It is never written to the DB, so the two underlying facts
  cannot drift.

Rejected alternatives:

- **Persist `status = "busy"` in the DB** (write it in `claim_agent`, revert in
  `release_agent`): creates a second source of truth that must be kept in sync
  across claim/release/disconnect/heartbeat paths; a missed reset can strand an
  agent. More risk, no gain.
- **Derive busy entirely in the frontend from `current_build_id`**: discards the
  existing frontend "busy" handling (badge variant + header count both key off
  `status === "busy"`), so it is more frontend churn and splits the busy logic
  across two places.

## Detailed design

### 1. API surface (`backend/app/schemas/agent.py`)

Add a small nested object and expose it on `AgentResponse`. `status` can now
emit `"busy"`; it is already typed as `str`, so no type change is required.

```python
class CurrentBuildInfo(BaseModel):
    id: uuid.UUID
    number: int
    pipeline_name: str


class AgentResponse(BaseModel):
    ...
    status: str                          # now also emits "busy"
    current_build: CurrentBuildInfo | None = None
```

`AgentRegistrationResponse` extends `AgentResponse`, so it inherits
`current_build`. At register / rotate time the agent cannot be busy, so it is
always `None` there — harmless.

### 2. Backend derivation (`backend/app/api/v1/agents.py`)

A single shared helper, applied in both `list_agents` and `get_agent` (mirroring
how `_normalize_status` is already used in both). The order of operations
matters:

1. Run the existing `_normalize_status` first (stale heartbeat → `offline`).
2. Collect `current_build_id` only for agents that are still `online` after
   step 1.
3. One **batched** query for those build ids: `Build.id`, `Build.number`, and
   `Pipeline.name`, joined via the existing `Build.pipeline` relationship,
   returned as a map keyed by build id.
4. For each agent whose build id is in the map: set the response
   `status = "busy"` and attach `current_build = {id, number, pipeline_name}` as
   a transient, in-memory value (never committed).

The DB `status` column, `current_build_id`, and `pick_online_agent` are all
untouched. Busy is purely computed on read.

### 3. Frontend (`frontend/src/app/agents/page.tsx`, `frontend/src/lib/api.ts`)

- Extend the `Agent` interface in `api.ts`:
  `current_build?: { id: string; number: number; pipeline_name: string } | null`.
- The status badge already flips to the amber "busy" variant via
  `agentStatusVariant` — no change needed there.
- Add one new row in the card's `CardContent` (styled like the existing
  os/arch/labels rows, with a lucide icon such as `Hammer`): a Next.js
  `<Link href={`/builds/${current_build.id}`}>` reading
  **"Building {pipeline_name} #{number}"**, rendered only when
  `agent.current_build` is set.
- Side benefit: the header's "X busy" counter starts reporting correctly for
  free, because the list response now reports busy.

### 4. Edge cases

- **Only online agents show busy** — busy is overlaid *after* normalization, so
  a stale/offline agent never shows busy even if a reservation lingered.
- **Build id not found** (build deleted, or a stale `current_build_id` left by a
  crash) — the batched query returns nothing for it, the agent stays `online`,
  no link is shown. Defensive; no error.
- **Disabled + busy** — the card already stacks a separate "disabled" badge
  under the status badge, so an agent finishing its current build while disabled
  correctly shows both "busy" and "disabled".
- The build's own status (running vs finished) is intentionally **not** checked.
  `current_build_id` is the authoritative reservation and the dispatch layer
  clears it on release, so showing busy reflects the reservation truthfully and
  self-heals.

### 5. Freshness

No new infrastructure. The agents page already reloads every 15 seconds, so the
busy state appears and clears within ~15s of a build starting/finishing. The new
data simply rides along in the existing list response.

## Testing / verification

The repository has no automated test suite. Verification is manual:

1. Register an agent and bring it online.
2. Trigger a build that routes to it.
3. Confirm the card flips to "busy" with a working "Building … #N" link within
   ~15s, and the header "busy" count increments.
4. Confirm the card returns to "online" (link gone) when the build finishes.

Optional: a minimal `pytest` covering just the derivation helper (online +
`current_build_id` set → `status == "busy"` with populated `current_build`;
offline with a lingering id → not busy; missing build id → not busy), if a
regression guard is wanted later.

## Out of scope

- Real-time/push updates for the badge (the 15s poll is sufficient).
- Showing per-step progress or live logs on the agent card.
- Any change to dispatch, claiming, or the stored `status` semantics.
