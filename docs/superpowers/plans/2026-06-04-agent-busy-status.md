# Agent "busy" status on the agents page card — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a "busy" status and a clickable "Building &lt;pipeline&gt; #&lt;number&gt;" link on an agent's card while it is executing a build.

**Architecture:** Approach A — derive busy at read time. The DB `Agent.status` column stays raw connectivity (`online`/`offline`); busyness already lives in `Agent.current_build_id`. The agents API overlays an effective `status: "busy"` plus a `current_build` object **only in the Pydantic response object** (never on the ORM column — `get_db` commits, so mutating the column would persist "busy" and strand the agent). The frontend already styles `"busy"`; it just needs to render the build link.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (backend), Pydantic v2, Next.js 16 / React 19 + TypeScript + Tailwind (frontend), lucide-react icons.

**Note on testing:** The repository has **no automated test harness** (no `pytest`, no test deps in `backend/pyproject.toml`; no frontend test runner). Per the approved spec, verification is by syntax/typecheck checks per task plus a manual end-to-end check at the end. Each backend task is verified with `python -m py_compile` (and a pure-Pydantic import where it needs no app config); each frontend task with `npm run lint` and `npm run build` (Next runs the TypeScript checker during build).

**Branch:** Work happens on `feature/agent-busy-status` (already created; the design spec is committed there).

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `backend/app/schemas/agent.py` | Modify | Add `CurrentBuildInfo` model; add `current_build` field to `AgentResponse`. |
| `backend/app/api/v1/agents.py` | Modify | Add `_build_agent_responses` helper (batched build lookup + busy overlay); wire into `list_agents` and `get_agent`. |
| `frontend/src/lib/api.ts` | Modify | Add `current_build` to the `Agent` interface. |
| `frontend/src/app/agents/page.tsx` | Modify | Import `Link` and `Hammer`; render the "Building …" link row on the card. |

---

## Task 1: Add the `CurrentBuildInfo` schema and `current_build` field

**Files:**
- Modify: `backend/app/schemas/agent.py`

- [ ] **Step 1: Add the `CurrentBuildInfo` model above `AgentResponse`**

In `backend/app/schemas/agent.py`, insert this class immediately **before** `class AgentResponse(BaseModel):` (currently at line 34):

```python
class CurrentBuildInfo(BaseModel):
    """The build an agent is currently reserved for.

    Computed at read time from ``Agent.current_build_id`` and surfaced on the
    agents page so the card can link to the running build. Never stored.
    """

    id: uuid.UUID
    number: int
    pipeline_name: str


```

- [ ] **Step 2: Add the `current_build` field to `AgentResponse`**

Still in `backend/app/schemas/agent.py`, find the `status: str` line inside `AgentResponse` (currently line 45) and add the `current_build` field directly after it:

```python
    status: str
    # Effective runtime build, present only when the agent is online and
    # holding a reservation. Computed in the API layer; never persisted.
    current_build: CurrentBuildInfo | None = None
```

(`AgentRegistrationResponse` extends `AgentResponse`, so it inherits this field; it is always `None` at register/rotate time.)

- [ ] **Step 3: Verify the schema imports and exposes the field**

This import touches only Pydantic (no app config), so it runs anywhere the backend deps are installed:

Run:
```bash
cd backend && python -c "from app.schemas.agent import AgentResponse, CurrentBuildInfo; assert 'current_build' in AgentResponse.model_fields; print('ok:', list(CurrentBuildInfo.model_fields))"
```
Expected output: `ok: ['id', 'number', 'pipeline_name']`

- [ ] **Step 4: Commit**

```bash
git add backend/app/schemas/agent.py
git commit -m "feat(agents): add current_build to AgentResponse schema"
```

---

## Task 2: Derive busy + current build in the agents API

**Files:**
- Modify: `backend/app/api/v1/agents.py`

- [ ] **Step 1: Add the new imports**

In `backend/app/api/v1/agents.py`, add the two model imports after the existing `from app.models.agent import Agent` line (line 16):

```python
from app.models.agent import Agent
from app.models.build import Build
from app.models.pipeline import Pipeline
```

Then add `CurrentBuildInfo` to the schema import block (currently lines 18–24) so it reads:

```python
from app.schemas.agent import (
    AgentCreate,
    AgentRegistrationResponse,
    AgentResponse,
    AgentUpdate,
    CurrentBuildInfo,
    HeartbeatRequest,
)
```

- [ ] **Step 2: Add the `_build_agent_responses` helper**

Insert this function immediately **after** `_normalize_status` (which ends at line 48, just before `@router.get("", ...)`):

```python
async def _build_agent_responses(
    agents: list[Agent], db: AsyncSession
) -> list[AgentResponse]:
    """Serialize agents to ``AgentResponse``, overlaying an effective
    ``status="busy"`` and the current build for online agents holding a
    reservation.

    The overlay lives ONLY in the response objects. We never assign
    ``agent.status = "busy"`` on the ORM instance, because ``get_db`` commits
    on success — persisting "busy" would break ``pick_online_agent`` (which
    requires ``status == "online"``) and would not be cleared by the
    heartbeat (which only resets offline -> online). Callers must run
    ``_normalize_status`` first so ``agent.status`` already reflects
    online/offline.
    """
    reserved_ids = {
        a.current_build_id for a in agents if a.current_build_id is not None
    }
    build_info: dict[uuid.UUID, CurrentBuildInfo] = {}
    if reserved_ids:
        rows = await db.execute(
            select(Build.id, Build.number, Pipeline.name)
            .join(Pipeline, Build.pipeline_id == Pipeline.id)
            .where(Build.id.in_(reserved_ids))
        )
        for build_id, number, pipeline_name in rows.all():
            build_info[build_id] = CurrentBuildInfo(
                id=build_id, number=number, pipeline_name=pipeline_name
            )

    responses: list[AgentResponse] = []
    for agent in agents:
        resp = AgentResponse.model_validate(agent)
        if agent.status == "online" and agent.current_build_id in build_info:
            resp = resp.model_copy(
                update={
                    "status": "busy",
                    "current_build": build_info[agent.current_build_id],
                }
            )
        responses.append(resp)
    return responses
```

- [ ] **Step 3: Wire it into `list_agents`**

Replace the body of `list_agents` (currently lines 51–67). The return type annotation changes from `list[Agent]` to `list[AgentResponse]`, and the final `return agents` becomes a call to the helper:

```python
@router.get("", response_model=list[AgentResponse])
async def list_agents(
    status_filter: str | None = Query(None, alias="status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("agents.read")),
) -> list[AgentResponse]:
    query = select(Agent).order_by(Agent.name).offset(skip).limit(limit)
    if status_filter:
        query = query.where(Agent.status == status_filter)

    result = await db.execute(query)
    agents = list(result.scalars().all())
    for agent in agents:
        _normalize_status(agent)
    return await _build_agent_responses(agents, db)
```

- [ ] **Step 4: Wire it into `get_agent`**

Replace `get_agent` (currently lines 126–137). The return annotation changes from `Agent` to `AgentResponse`, and the final `return _normalize_status(agent)` becomes normalize-then-build:

```python
@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("agents.read")),
) -> AgentResponse:
    agent = await db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    _normalize_status(agent)
    responses = await _build_agent_responses([agent], db)
    return responses[0]
```

- [ ] **Step 5: Verify the module compiles**

Run:
```bash
cd backend && python -m py_compile app/api/v1/agents.py && echo "py_compile ok"
```
Expected output: `py_compile ok` (no traceback).

If the backend stack is running under Compose, also confirm it imports cleanly with full config:
```bash
docker compose exec backend python -c "import app.api.v1.agents; print('import ok')"
```
Expected output: `import ok` (skip this sub-check if the stack is not up — Task 5 covers runtime).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/agents.py
git commit -m "feat(agents): derive busy status and current build in agents API"
```

---

## Task 3: Add `current_build` to the frontend `Agent` type

**Files:**
- Modify: `frontend/src/lib/api.ts`

- [ ] **Step 1: Extend the `Agent` interface**

In `frontend/src/lib/api.ts`, find the `Agent` interface (starts at line 688) and add the `current_build` field directly after the `status: AgentStatus | string;` line (line 697):

```typescript
  status: AgentStatus | string;
  // The build this agent is currently running. Present only when busy;
  // computed server-side so the card can link to the build.
  current_build?: {
    id: string;
    number: number;
    pipeline_name: string;
  } | null;
```

- [ ] **Step 2: Verify it typechecks**

Run:
```bash
cd frontend && npm run lint
```
Expected: completes with no errors for `src/lib/api.ts` (warnings unrelated to this change are fine).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(agents): add current_build to frontend Agent type"
```

---

## Task 4: Render the "Building …" link on the agent card

**Files:**
- Modify: `frontend/src/app/agents/page.tsx`

- [ ] **Step 1: Add the `Link` import**

In `frontend/src/app/agents/page.tsx`, add the Next.js `Link` import right after the React import (line 3):

```typescript
import * as React from "react";
import Link from "next/link";
```

- [ ] **Step 2: Add the `Hammer` icon to the lucide import**

In the same file, add `Hammer,` to the `lucide-react` import block (lines 6–18). For example, place it alphabetically near the top:

```typescript
import {
  Server,
  Plus,
  Trash2,
  Copy,
  Check,
  Cpu,
  Hammer,
  KeyRound,
  Monitor,
  Power,
  RefreshCw,
  Tag,
} from "lucide-react";
```

- [ ] **Step 3: Render the build link as the first row of card content**

Find the agent card's content container (line 703): `<CardContent className="space-y-3 text-sm">`. Insert this block as its **first** child, immediately before the `{(agent.os || agent.arch) && (` block (line 704):

```tsx
                <CardContent className="space-y-3 text-sm">
                  {agent.current_build && (
                    <Link
                      href={`/builds/${agent.current_build.id}`}
                      className="flex items-center gap-1.5 font-medium text-primary hover:underline"
                    >
                      <Hammer className="h-3.5 w-3.5 shrink-0" />
                      <span className="truncate">
                        Building {agent.current_build.pipeline_name} #
                        {agent.current_build.number}
                      </span>
                    </Link>
                  )}
                  {(agent.os || agent.arch) && (
```

(The status badge already turns amber and reads "busy" automatically via `agentStatusVariant`, and the header "X busy" counter starts reporting correctly — no change needed for either.)

- [ ] **Step 4: Verify lint passes**

Run:
```bash
cd frontend && npm run lint
```
Expected: no errors for `src/app/agents/page.tsx`.

- [ ] **Step 5: Verify the production build (TypeScript check) passes**

Run:
```bash
cd frontend && npm run build
```
Expected: build completes successfully ("Compiled successfully" / route list printed), with no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/agents/page.tsx
git commit -m "feat(agents): show current build link on busy agent cards"
```

---

## Task 5: End-to-end manual verification

**Files:** none (runtime verification only).

This requires the full stack (Postgres, Redis, backend, Celery worker, an agent, frontend). Use the project's Compose/Makefile workflow.

- [ ] **Step 1: Bring up the stack and the frontend**

```bash
make up            # backend, worker, db, redis (per repo README)
cd frontend && npm run dev   # if not already served by the stack
```
Expected: backend reachable, agents page loads at the app URL.

- [ ] **Step 2: Register an agent and bring it online**

Use the Agents page "Register Agent" flow, then start the agent with the printed snippet (binary or Docker). Confirm the agent card shows the green **online** badge.

- [ ] **Step 3: Trigger a build that routes to the agent**

Trigger a build on a pipeline whose `runs_on` matches the agent (or any pipeline if the agent has no constraints).

- [ ] **Step 4: Observe the busy state (≤ ~15s)**

Expected on the agent's card within one 15s refresh cycle:
- Status badge turns amber and reads **busy**.
- A new first row shows **Building &lt;pipeline-name&gt; #&lt;number&gt;** with a hammer icon.
- Clicking it navigates to `/builds/<id>` (the running build's page).
- The header summary increments **"… busy"**.

- [ ] **Step 5: Observe it clears on completion**

When the build finishes, expected within one refresh cycle:
- Status badge returns to green **online**.
- The "Building …" row disappears.
- Header "busy" count returns to 0 (for that agent).

- [ ] **Step 6 (regression): confirm scheduling still works after busy**

Trigger a second build on the same agent after the first completes. Expected: it dispatches and runs normally — confirming the `status` column was never persisted as "busy" (the agent remained schedulable).

- [ ] **Step 7: Final confirmation**

No commit needed (no code change). If any step failed, return to the relevant task; otherwise the feature is complete.

---

## Self-Review (completed by plan author)

- **Spec coverage:**
  - API surface (`CurrentBuildInfo` + `current_build`) → Task 1. ✅
  - Backend derivation helper, normalize-first ordering, batched build+pipeline query, online-only overlay → Task 2. ✅
  - Frontend `Agent` type + card link + reused badge/header → Tasks 3–4. ✅
  - Edge cases (only-online shows busy; missing build id → not busy; disabled+busy stacks badges) → handled by the helper's `status == "online"` guard and `in build_info` check (Task 2), and the existing separate disabled badge (no change). ✅
  - 15s freshness, no new infra → no task needed; data rides the existing list response. ✅
  - Manual verification (no harness) → Task 5. ✅
- **Placeholder scan:** No TBD/TODO; every code step shows complete code. ✅
- **Type/name consistency:** `CurrentBuildInfo` fields (`id`, `number`, `pipeline_name`) match across the schema (Task 1), the helper's construction (Task 2), the frontend type (Task 3), and the card render (Task 4). Helper name `_build_agent_responses` is used consistently in both endpoints. ✅
- **Refinement vs spec:** The spec said to set the response status to "busy" as a "transient, never committed" value; this plan pins the mechanism to a Pydantic `model_copy` overlay (not ORM mutation) because `get_db` commits on success. Faithful to the spec's intent.
