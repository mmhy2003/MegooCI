# Project Members Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin-only **Members** tab on the project detail page to view/manage a project's members (add a user with a role, change role, remove), reusing the existing assign/remove endpoints.

**Architecture:** Backend adds one field (`user_role_id`) to the existing `GET /projects/{id}/members` response so removal/role-change have the row id. Frontend adds a project-centric `ProjectMembersPanel` component (the inverse of the existing user-centric `ProjectAssignmentsEditor`) that reuses `usersApi.assignRole` (upsert = add/change) and `usersApi.removeRole`, surfaced as a new tab rendered only for admins.

**Tech Stack:** FastAPI + SQLAlchemy 2 async (backend); Next.js + TypeScript + sonner toast + `@/components/ui` primitives (frontend); pytest (`asyncio_mode=auto`, in-memory SQLite via `tests/_rbac.py`); `npx tsc --noEmit` for the frontend gate.

## Global Constraints

- **Admin-only.** Membership management requires global `users.manage`. The Members tab is rendered only for admins (`usePermission("users.manage")`); a non-admin never sees it. Do NOT add a delegated/project-scoped management path (deliberate non-goal).
- **Reuse endpoints, add one field.** Add `user_role_id` to `ProjectMemberResponse`; do NOT add new `POST`/`DELETE /projects/{id}/members` endpoints. Add/change uses `usersApi.assignRole(userId, {role_id, scope_type:"project", scope_id})` (the backend upserts one role per (user, project)); remove uses `usersApi.removeRole(userId, user_role_id)`.
- **Roles:** developer / viewer only (the `admin` role is never project-scoped — the backend already rejects it). The add picker excludes `is_admin` users and users already in the members list.
- Backend tests: in-memory SQLite via `tests/_rbac.py` helpers (`build_inmemory_factory`, `seed_project`, `seed_user(db)`, `seed_role(db, name, permissions)`); run with `./.venv/Scripts/python.exe -m pytest` from `backend/`. `seed_role` is raw-SQL (don't ORM-commit a `Role(permissions=[...])`); committing a `UserRole` via ORM is fine (no array column).
- Frontend gate: `npx tsc --noEmit` clean (the repo has no frontend unit-test harness; `npm run lint` is broken under Next 16 — do not use it).
- Follow existing patterns: the new component mirrors `frontend/src/components/ProjectAssignmentsEditor.tsx` (sonner `toast`, `Badge`/`Button`/`Select` from `@/components/ui`, the `{ body?: { detail?: string } }` error-detail extraction).

---

### Task 1: Backend — surface `user_role_id` on project members

**Files:**
- Modify: `backend/app/schemas/project.py` (`ProjectMemberResponse`)
- Modify: `backend/app/api/v1/projects.py` (`list_project_members`)
- Test: `backend/tests/test_project_members.py` (new)

**Interfaces:**
- Produces: `GET /projects/{id}/members` returns rows shaped `{user_role_id: UUID, user_id: UUID, email: str, name: str, role_name: str}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_project_members.py`:

```python
import os
import uuid

import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._rbac import build_inmemory_factory, seed_project, seed_user, seed_role


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_project_members_includes_user_role_id(sf):
    from app.api.v1.projects import list_project_members
    from app.models.role import UserRole

    async with sf() as db:
        pid = await seed_project(db, "P")
        uid = await seed_user(db)
        rid = await seed_role(db, "developer", ["pipelines.read"])
        ur_id = uuid.uuid4()
        db.add(UserRole(id=ur_id, user_id=uid, role_id=rid,
                        scope_type="project", scope_id=pid))
        await db.commit()

    async with sf() as db:
        members = await list_project_members(pid, db=db, _current_user=None)

    assert len(members) == 1
    m = members[0]
    assert m["user_role_id"] == ur_id
    assert m["user_id"] == uid
    assert m["role_name"] == "developer"
    assert m["email"] and m["name"]


async def test_list_project_members_only_this_project(sf):
    from app.api.v1.projects import list_project_members
    from app.models.role import UserRole

    async with sf() as db:
        p1 = await seed_project(db, "P1")
        p2 = await seed_project(db, "P2")
        uid = await seed_user(db)
        rid = await seed_role(db, "viewer", ["pipelines.read"])
        db.add(UserRole(id=uuid.uuid4(), user_id=uid, role_id=rid,
                        scope_type="project", scope_id=p2))
        await db.commit()

    async with sf() as db:
        members = await list_project_members(p1, db=db, _current_user=None)

    assert members == []
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_members.py -v`
Expected: FAIL — `KeyError: 'user_role_id'` (the endpoint doesn't return it yet).

- [ ] **Step 2: Run test to verify it fails**

Run the command above. Expected: `test_list_project_members_includes_user_role_id` fails on the missing `user_role_id` key; `test_list_project_members_only_this_project` may already pass.

- [ ] **Step 3: Add the field to the schema**

In `backend/app/schemas/project.py`, change `ProjectMemberResponse` to:

```python
class ProjectMemberResponse(BaseModel):
    user_role_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    name: str
    role_name: str
```

(Confirm `uuid` is already imported in this file — it is, used by other schemas.)

- [ ] **Step 4: Return it from the endpoint**

In `backend/app/api/v1/projects.py`, update `list_project_members` to select and return `UserRole.id`:

```python
@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
async def list_project_members(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("users.manage")),
) -> list[dict]:
    from app.models.role import Role, UserRole
    from app.models.user import User as UserModel
    rows = await db.execute(
        select(UserRole.id, UserModel.id, UserModel.email, UserModel.name, Role.name)
        .join(UserModel, UserRole.user_id == UserModel.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.scope_type == "project", UserRole.scope_id == project_id)
        .order_by(UserModel.email)
    )
    return [
        {"user_role_id": ur_id, "user_id": uid, "email": email, "name": name, "role_name": rn}
        for ur_id, uid, email, name, rn in rows.all()
    ]
```

(Note the join is rewritten to start from `UserRole` so `UserRole.id` is the first selected column; the `where`/`order_by` are unchanged. `ProjectMemberResponse` must be imported in this file — it already is, from the Task 7 work that added this endpoint.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_members.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the full suite for no regression**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (the existing assignment-API tests still green; member shape change is additive).

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/project.py backend/app/api/v1/projects.py backend/tests/test_project_members.py
git commit -m "feat(rbac): include user_role_id in project members response"
```

---

### Task 2: Frontend — `ProjectMembersPanel` + Members tab

**Files:**
- Modify: `frontend/src/lib/api.ts` (`projectsApi.members` return type)
- Create: `frontend/src/components/ProjectMembersPanel.tsx`
- Modify: `frontend/src/app/projects/[id]/page.tsx` (Tab union, `tabs` array, render, import)

**Interfaces:**
- Consumes: `projectsApi.members(projectId)` → now `{user_role_id, user_id, email, name, role_name}[]`; `usersApi.list()` → `UserDetail[]` (has `id`, `email`, `name`, `is_admin`); `rolesApi.list()` → `Role[]`; `usersApi.assignRole(userId, {role_id, scope_type, scope_id})`; `usersApi.removeRole(userId, userRoleId)`; `usePermission("users.manage")`.

- [ ] **Step 1: Update the members API client return type**

In `frontend/src/lib/api.ts`, change `projectsApi.members` to include `user_role_id`:

```ts
  members: (projectId: string) =>
    fetchApi<
      { user_role_id: string; user_id: string; email: string; name: string; role_name: string }[]
    >(`/api/v1/projects/${projectId}/members`),
```

- [ ] **Step 2: Create the `ProjectMembersPanel` component**

Create `frontend/src/components/ProjectMembersPanel.tsx`:

```tsx
"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { X } from "lucide-react";
import {
  usersApi,
  projectsApi,
  rolesApi,
  type UserDetail,
  type Role,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";

type Member = {
  user_role_id: string;
  user_id: string;
  email: string;
  name: string;
  role_name: string;
};

export function ProjectMembersPanel({ projectId }: { projectId: string }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [users, setUsers] = useState<UserDetail[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [addUserId, setAddUserId] = useState("");
  const [addRoleId, setAddRoleId] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    projectsApi.members(projectId).then(setMembers).catch(() => {});
    usersApi.list().then(setUsers).catch(() => {});
    rolesApi
      .list()
      .then((rs) => setRoles(rs.filter((r) => r.name !== "admin")))
      .catch(() => {});
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  function roleIdForName(name: string): string {
    return roles.find((r) => r.name === name)?.id ?? "";
  }

  // Users eligible to add: not admins, not already members.
  const memberIds = new Set(members.map((m) => m.user_id));
  const availableUsers = users.filter((u) => !u.is_admin && !memberIds.has(u.id));

  function detail(e: unknown, fallback: string): string {
    return (
      (e as { body?: { detail?: string } })?.body?.detail ||
      (e instanceof Error ? e.message : fallback)
    );
  }

  async function handleAdd() {
    if (!addUserId || !addRoleId) return;
    setBusy(true);
    try {
      await usersApi.assignRole(addUserId, {
        role_id: addRoleId,
        scope_type: "project",
        scope_id: projectId,
      });
      setAddUserId("");
      setAddRoleId("");
      load();
    } catch (e: unknown) {
      toast.error(detail(e, "Failed to add member"));
    } finally {
      setBusy(false);
    }
  }

  async function handleChangeRole(userId: string, roleId: string) {
    if (!roleId) return;
    setBusy(true);
    try {
      await usersApi.assignRole(userId, {
        role_id: roleId,
        scope_type: "project",
        scope_id: projectId,
      });
      load();
    } catch (e: unknown) {
      toast.error(detail(e, "Failed to change role"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(userId: string, userRoleId: string) {
    setBusy(true);
    try {
      await usersApi.removeRole(userId, userRoleId);
      load();
    } catch (e: unknown) {
      toast.error(detail(e, "Failed to remove member"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-2">
        {members.length === 0 && (
          <p className="text-sm text-muted-foreground italic">No members yet.</p>
        )}
        {members.map((m) => (
          <div
            key={m.user_role_id}
            className="flex flex-wrap items-center gap-3 rounded-md border p-3"
          >
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{m.name}</p>
              <p className="truncate text-xs text-muted-foreground">{m.email}</p>
            </div>
            <div className="w-32">
              <Select
                value={roleIdForName(m.role_name)}
                onChange={(e) => handleChangeRole(m.user_id, e.target.value)}
                disabled={busy}
                options={roles.map((r) => ({ value: r.id, label: r.name }))}
              />
            </div>
            <Button
              size="sm"
              variant="outline"
              disabled={busy}
              onClick={() => handleRemove(m.user_id, m.user_role_id)}
              className="text-destructive hover:text-destructive"
            >
              <X className="mr-1 h-3.5 w-3.5" />
              Remove
            </Button>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t pt-4">
        <div className="w-56">
          <Select
            value={addUserId}
            onChange={(e) => setAddUserId(e.target.value)}
            placeholder="Select user…"
            disabled={busy}
            options={availableUsers.map((u) => ({
              value: u.id,
              label: u.name ? `${u.name} (${u.email})` : u.email,
            }))}
          />
        </div>
        <div className="w-32">
          <Select
            value={addRoleId}
            onChange={(e) => setAddRoleId(e.target.value)}
            placeholder="Role…"
            disabled={busy}
            options={roles.map((r) => ({ value: r.id, label: r.name }))}
          />
        </div>
        <Button
          size="sm"
          disabled={busy || !addUserId || !addRoleId}
          onClick={handleAdd}
        >
          Add member
        </Button>
      </div>
    </div>
  );
}
```

> If `UserDetail`, `Role`, or the `Select`/`Button` import paths differ from what `ProjectAssignmentsEditor.tsx` uses, match that file exactly (it's the reference).

- [ ] **Step 3: Wire the Members tab into the project page**

In `frontend/src/app/projects/[id]/page.tsx`:

(a) Add the import near the other component imports:

```tsx
import { ProjectMembersPanel } from "@/components/ProjectMembersPanel";
```

(b) Extend the `Tab` union (line ~50):

```tsx
type Tab = "pipelines" | "integrations" | "members" | "settings";
```

(c) Add the admin permission check near the other `usePermission` calls (line ~57):

```tsx
  const canManageUsers = usePermission("users.manage");
```

(d) Add the Members tab to the `tabs` array (line ~388), admin-gated, between Integrations and Settings:

```tsx
  const tabs: { key: Tab; label: string }[] = [
    { key: "pipelines", label: "Pipelines" },
    { key: "integrations", label: "Integrations" },
    ...(canManageUsers ? [{ key: "members" as Tab, label: "Members" }] : []),
    { key: "settings", label: "Settings" },
  ];
```

(e) Render the panel where the other tab panels render (after the `integrations` panel, line ~583), guarded by the admin check:

```tsx
        {activeTab === "members" && canManageUsers && (
          <ProjectMembersPanel projectId={id} />
        )}
```

(Use `id` — the route param the page already passes to `ProjectIntegrations projectId={id}`.)

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/ProjectMembersPanel.tsx "frontend/src/app/projects/[id]/page.tsx"
git commit -m "feat(rbac): admin-only project Members tab"
```

---

### Task 3: Verification sweep

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all prior tests + the new `test_project_members.py`).

- [ ] **Step 2: Frontend typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 3: Manual smoke (optional, live stack)**

As an admin, open a project → **Members** tab: confirm current members list with their roles; add a non-admin user with developer; change their role to viewer; remove them. Confirm the admin and existing members don't appear in the Add picker. Log in as a non-admin: confirm the Members tab is absent on the project page.

---

## Notes for the implementer

- **Why no new endpoints:** the user-centric `assignRole` (upsert — one role per user+project) and `removeRole` already do add/change/remove; the only missing piece was the `user_role_id` for removal, added in Task 1. Keep it that way (DRY; admin-only).
- **Role change is an upsert:** `assignRole` with the same `(user, project)` replaces the role — that's exactly the change-role behavior, no separate endpoint needed.
- **Admin-gating is double:** the tab is only listed when `canManageUsers`, and the panel render is also `&& canManageUsers` — a non-admin can't reach it even by forcing `activeTab`.
