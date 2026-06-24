# Project Members tab — design

**Date:** 2026-06-24
**Status:** Approved (pending spec review)

## Problem

Project assignments (who can access a project, and with which role) can only be
managed from the **admin Users page** — user-centric: pick a user, then add
projects to them. There is no project-centric view: open a project and see/manage
who's on it. The backend already supports this (`GET /projects/{id}/members`,
built in the project-scoped-access feature) but the spec marked the project-side
tab *optional*, so only the user-centric UI shipped.

This adds a **Members tab** on the project detail page so an admin can manage a
project's membership in place.

## Goals

- On the project detail page, an admin can see the project's members
  (`{user, role}`), add a user with a role, change a member's role, and remove a
  member — without leaving the project.
- Reuse the existing, tested assignment endpoints; minimal new surface.

## Non-goals

- **Delegated membership management** — only admins (`users.manage`) manage
  members. A project-scoped user (even a project "developer") cannot. This
  preserves the existing deliberate non-goal "project-scoped users cannot manage
  membership."
- Inviting brand-new users by email from the project page (that stays on the
  invites flow).

## Decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Who can manage | **Admins only** (`users.manage`). The Members tab is rendered only for admins. |
| 2 | API approach | **Reuse** the user-centric `assignRole`/`removeRole` endpoints; add `user_role_id` to the members response so removal/role-change have the row id. No new endpoints. |
| 3 | Role per member | developer or viewer (the `admin` role is never project-scoped — backend already rejects it). |
| 4 | Add-member picker | Lists users from `usersApi.list()`, excluding admins and users already members of this project. |

## Architecture — reuse existing endpoints + one new response field

### Backend (one field)

- `ProjectMemberResponse` ([backend/app/schemas/project.py](../../../backend/app/schemas/project.py))
  gains `user_role_id: uuid.UUID`.
- `list_project_members` ([backend/app/api/v1/projects.py](../../../backend/app/api/v1/projects.py))
  already joins `UserRole` to filter `scope_type='project' AND scope_id=project_id`;
  add `UserRole.id` to the `SELECT` and include it in each returned row. Auth stays
  `require_permission("users.manage")`.

Resulting member shape: `{user_role_id, user_id, email, name, role_name}`.

The add / change / remove actions reuse the existing user-centric endpoints:

- **Add / change role:** `POST /users/{user_id}/roles` with
  `{role_id, scope_type:"project", scope_id: project_id}` — already validates the
  project, rejects `admin` at project scope, and **upserts** one role per
  (user, project), so the same call covers both adding and changing.
- **Remove:** `DELETE /users/{user_id}/roles/{user_role_id}` — needs the
  `user_role_id` now surfaced by the members response.

### Frontend

- `projectsApi.members` ([frontend/src/lib/api.ts](../../../frontend/src/lib/api.ts))
  return type gains `user_role_id: string`.
- **New component `ProjectMembersPanel`**
  (`frontend/src/components/ProjectMembersPanel.tsx`) — the project-centric inverse
  of the existing user-centric `ProjectAssignmentsEditor`:
  - Props: `{ projectId: string }`.
  - On mount, fetch `projectsApi.members(projectId)`, `usersApi.list()`, and the
    non-admin roles via `rolesApi.list()` (filter out `admin`).
  - Render each member as a row: name/email · role, with a **role `<select>`**
    (change → `usersApi.assignRole(user_id, {role_id, scope_type:"project", scope_id: projectId})`,
    the upsert) and a **Remove** button
    (`usersApi.removeRole(user_id, user_role_id)`).
  - **Add-member row:** a user `<select>` (from `usersApi.list()`, excluding
    `is_admin` users and users already in the members list) + a role `<select>`
    (developer/viewer) + **Add** → `assignRole`. Disable Add until both selected.
  - Refetch members after every add/change/remove; a `busy` flag disables
    controls during in-flight requests. Errors surface via the existing toast
    pattern.
  - Follow the project's existing UI primitives (Badge/Button/Select) as used
    elsewhere on the project page.
- **Members tab** on the project detail page
  ([frontend/src/app/projects/[id]/page.tsx](../../../frontend/src/app/projects/[id]/page.tsx)):
  - Extend the `Tab` union from `"pipelines" | "integrations" | "settings"` to
    also include `"members"`.
  - Render the Members tab button and its panel **only when the current user is an
    admin** (`usePermission("users.manage")`), matching the existing tab styling.
  - The panel body renders `<ProjectMembersPanel projectId={project.id} />`.

## Edge cases

- **Empty membership** → the panel shows "No members yet" with the add row still
  available.
- **Non-admin viewing the project** → the Members tab is not rendered at all (no
  data fetch, no 403 surfaced).
- **A user already a member** → excluded from the add picker; their row offers
  change-role / remove instead (no duplicate assignment; the backend upsert would
  collapse it anyway).
- **Admin users** → excluded from the add picker (a project role on a global admin
  is a no-op; admins already see all projects).

## Testing

- **Backend:** extend the existing `list_project_members` test (or add one) to
  assert each returned member includes a non-null `user_role_id` matching the
  project-scoped `UserRole` row.
- **Frontend:** `npx tsc --noEmit` clean. (No frontend unit-test harness in this
  repo; the build/typecheck is the gate, consistent with prior frontend tasks.)

## Out of scope

- Delegated (non-admin) membership management.
- Email invites from the project page.
- A members count/badge on the project list page.
