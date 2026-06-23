# Project-scoped access: assign projects to users — design

**Date:** 2026-06-23
**Status:** Approved (pending spec review)

## Problem

MegooCI has rich RBAC — granular permissions, system roles (`admin`,
`developer`, `viewer`), and a `UserRole` table that already supports
**scoped** assignments (`scope_type`/`scope_id`) plus enforcement helpers
(`check_scoped_permission`, `effective_scoped_permissions`). But nothing uses
the scoping for *visibility*:

- `GET /projects`, `GET /pipelines`, `GET /builds` return **everything** and
  gate on **global** permissions (`require_permission("…read")`). A user whose
  access is only project-scoped has no global read permission, so these
  endpoints **403 before any scoped check runs** — there is no filtering path
  for scoped users at all.
- Individual pipeline/build/log/secret reads also gate on global permissions.
- Every non-admin is created with a **global** `developer`/`viewer` role, so
  in practice everyone sees all projects.

We want admins to **assign specific projects to specific users** so each
non-admin sees only the projects they're assigned to — and, transitively,
only the pipelines, builds, artifacts, git repos, and secrets under those
projects.

## Goals

- Admins (global `admin` role or `is_admin`) see and manage everything.
- A non-admin sees only projects they are explicitly assigned to, and acts on
  each per the role of that assignment (`developer` = manage, `viewer` =
  read-only).
- **One user can be assigned to many projects**, with a possibly different
  role per project (developer on A, viewer on B).
- Assignment-gated visibility cascades to pipelines, builds, artifacts, git
  project repositories, project-scoped secrets/env vars, and search/dashboard
  results.
- Smooth transition: existing users keep their current visibility.

## Decisions

| # | Decision | Choice |
|---|---|---|
| 1 | Visibility model | **Assignment-gated.** Admins global; non-admins see only assigned projects (zero assignments ⇒ see nothing). |
| 2 | Role granularity | **Per-project role.** Reuse scoped `UserRole` (`scope_type="project"`, `scope_id`, `role_id`). Non-admins have **no** global role. No new table. |
| 3 | Scope breadth | Pipelines, builds (+ logs/artifacts/triggers), **git project repositories**, **project-scoped secrets/env vars**, **search/dashboard**. |
| 4 | Existing users | **Migrate, preserve access**: convert each global `developer`/`viewer` role into per-project rows across all *current* projects, then drop the global row. |
| 5 | Container registry | **Out of scope** — unchanged. Gated solely by existing global `registry.*` perms, which non-admins lack, so they see no registry. `registry.py` is not touched. |
| 6 | Project-record lifecycle | A project-scoped `developer` (carries `projects.manage`) **can** rename/delete that project. Creating a project stays global `projects.manage` (admin). |
| 7 | One role per (user, project) | Enforced at the API/UI: re-assigning replaces the prior role (upsert), never stacking developer+viewer. |
| 8 | Project hierarchy | **No inheritance** — assignment is per-project; a parent assignment does not grant children. Future enhancement. |

## Architecture — central access helper + per-endpoint filtering

(Chosen over reusable dependencies and a visibility-query service: lowest
abstraction, matches the codebase's existing inline `check_scoped_permission`
style, and is the most unit-testable.)

### Access core — `app/core/access.py`

```python
ALL_PROJECTS = <sentinel>  # "unrestricted"

def accessible_project_ids(user, permission) -> set[UUID] | ALL_PROJECTS:
    """Projects in which *user* effectively holds *permission*.

    - Admin (is_admin or global "admin") or any GLOBAL grant of *permission*
      (via effective_permissions, which already applies any PAT scope)
      -> ALL_PROJECTS.
    - Otherwise -> { scope_id of each project-scoped UserRole whose role
      grants *permission* }, validated through effective_scoped_permissions
      so PAT scope still caps it. No matches -> empty set.
    """
```

Computed from the already-`selectinload`ed `user.user_roles` (no extra query
for the permission logic). Composes with — does not replace — the existing
`effective_permissions`, `effective_scoped_permissions`, and
`check_scoped_permission` in `app/core/deps.py`.

### Resource → project resolvers (one tested place, in `access.py`)

`pipeline→project_id` (direct), `build→project_id` (via pipeline),
`artifact→project_id` (via build→pipeline), `secret/env→project_id`
(`scope_id` when `scope_type="project"`). (Git repos need no resolver — their
`project_id` is in the URL path.) Detail endpoints use these to know which
project to `check_scoped_permission` against; list endpoints use them for the
join/filter column.

### List-endpoint pattern (uniform)

Replace `Depends(require_permission("X.read"))` with
`Depends(get_current_active_user)`, then:

```python
pids = accessible_project_ids(user, "X.read")
if pids is ALL_PROJECTS:      # no filter
    ...
elif not pids:                # zero assignments
    return []                 # never 403
else:
    query = query.where(<project column> .in_(pids))
```

### Detail / mutate pattern

`Depends(get_current_active_user)` → resolve the resource's `project_id` via a
resolver → `check_scoped_permission(user, perm, "project", project_id)` (403 on
miss, consistent with today). Create-of-a-new-project stays global
`projects.manage`.

## Endpoint enforcement matrix

| Resource (file) | List filter | Detail / mutate scoped check |
|---|---|---|
| **Projects** `projects.py` | `accessible(projects.read)` | get/update/delete → `projects.read`/`manage` on the project (relax global dep; scoped check already present on update/delete, add to get). **Create** stays global `projects.manage`. |
| **Pipelines** `pipelines.py` | `accessible(pipelines.read)`; `?project_id` outside the set ⇒ `[]` | get/update/delete via `pipeline→project`; create → scoped `pipelines.manage` on `body.project_id`. |
| **Builds** `builds.py` | builds whose `pipeline→project ∈ accessible(builds.read)`; `?pipeline_id` checked | get/logs → `builds.read`; trigger/cancel/retry/delete → `builds.manage`, via `build/pipeline→project`. |
| **Artifacts** `artifacts.py` | via `build→pipeline→project` | read/manage via same resolver. **Agent-token uploads keep their own auth — untouched.** |
| **Git repos** `project_repositories.py` | `project_id` is in the path → scoped check directly | read = `projects.read`, mutate = `projects.manage` on the path project. |
| **Secrets/env** `secrets.py` | project-scoped rows where `scope_id ∈ accessible(secrets.read)`; **global-scoped rows only if the user has *global* `secrets.read`** (⇒ admin-only) | scoped `secrets.read`/`manage` on the row's project. |
| **Search** `search.py` | every entity filtered through `accessible(<entity>.read)`; needs `project_id` filterable on pipeline/build/project docs | n/a |
| **Dashboard / counts** | same accessible-project filter (most ride the filtered lists) | n/a |
| **AI assistant** `ai_assistant.py` | n/a | project-context endpoints scope-check accessible project access |
| **Container registry** `registry.py` | **unchanged** | **unchanged** |

## Assignment management

### API (`users.py`, `projects.py`, schemas in `roles.py`)

- `POST /users/{id}/roles` (exists): when `scope_type="project"`, validate
  `scope_id` references a real project; **reject the `admin` role at project
  scope**; enforce **one role per (user, project)** — if a project-scoped row
  already exists for that user+project, replace its `role_id` (upsert) instead
  of returning 409.
- `DELETE /users/{id}/roles/{user_role_id}` (exists): unchanged.
- `GET /users/{id}` and the user list: enrich each role entry with `scope_id`
  and the **project name** for display.
- **New** `GET /projects/{id}/members`: list `{user, role}` for a project
  (global `users.manage`/`users.read`), powering a project-side Members view.
- All assignment management requires **global `users.manage`** (admins). A
  deliberate non-goal: project-scoped users cannot manage membership.

### User creation / invite transition

`create_user` (`users.py`) and invite-accept (`invites.py`) today assign a
**global** role. New rule: **admin** choice → global `admin` row +
`is_admin=True` (unchanged); **non-admin** → create the user / accept the
invite with **no global role**, then the admin assigns projects via the
endpoints above. `UserCreateRequest` / invite flows change from "pick a global
role" to "admin yes/no," with project assignment as the follow-up step.

### Frontend (`settings/page.tsx` user management)

- Each user expands to their assignments: `{project, role}` chips with add
  (project picker + developer/viewer), change-role, and remove.
- Create-user: **Admin** vs **Member**; for Members, optionally seed initial
  project assignments.
- `projects` / `pipelines` / `builds` pages need **no logic change** (they
  filter via the now-scoped API). Add a friendly empty state for non-admins
  with zero assignments ("No projects assigned yet — ask an admin").
- Optional **Members** tab on project detail, backed by
  `GET /projects/{id}/members`.

"Display pipelines and builds related to them" means the **logged-in user**
sees only their accessible projects' pipelines/builds everywhere — self-scoped
visibility via list filtering, not an admin "view-as-another-user" mode.

## Migration (existing users)

A data migration (Alembic):

1. For every `UserRole` with `scope_type="global"` whose role is `developer`
   or `viewer` (not `admin`): insert a project-scoped row of that same role for
   **every existing project**, then delete the global row.
2. Global `admin` rows and `is_admin` are untouched.

Result: current visibility is preserved; projects created *after* the
migration require explicit assignment (the new behavior). Downgrade
re-creates a single global role per affected user (best-effort).

## Edge cases

- **Project deletion must clean up assignments.** `UserRole.scope_id` has *no
  FK* to projects, so deleting a project would orphan its project-scoped rows.
  In `delete_project` (normal **and** the force-cascade path) add:
  `DELETE FROM user_roles WHERE scope_type='project' AND scope_id=:project_id`.
- **Zero assignments** → all lists `[]`, friendly empty state, never 403;
  profile/password still work.
- **PAT scope** composes — a token can't broaden a non-admin beyond their
  assigned projects (reuses `_apply_token_scope`).
- **Inaccessible detail read** → 403 (consistent with today's
  `check_scoped_permission`; accepts minor existence disclosure rather than
  404-masking everywhere).
- **`?project_id` / `?pipeline_id` outside the accessible set** on a list
  endpoint → empty result (don't leak existence).
- **Global non-admin roles** still technically grant "see all" (a global grant
  ⇒ `ALL_PROJECTS`). The migration removes existing ones and the UI won't
  offer creating new ones, but the system doesn't forbid them.

## Testing

In-memory SQLite with the existing `@compiles` type shims; same patterns as
`test_agent_dispatch.py` / `test_pipeline_cascade_delete.py`.

**Unit (`access.py`):**
- `accessible_project_ids`: admin → `ALL_PROJECTS`; global grant → `ALL_PROJECTS`;
  scoped developer → its projects for both read and manage; scoped viewer →
  read only (manage set excludes it); PAT scope caps the set; zero → empty.
- Resolvers: `build→project`, `artifact→project`, `secret→project`.

**Endpoint:**
- Lists (projects/pipelines/builds/secrets) filtered: non-admin sees only
  assigned, admin sees all, zero → `[]`.
- Detail read of an inaccessible pipeline/build → 403; accessible → ok.
- Scoped mutate: developer-on-project can trigger a build / edit a pipeline;
  viewer-on-project → 403; non-member → 403.
- Global-scoped secret hidden from a non-admin; project secret visible to its
  members.
- Assignment: project-scope assign validates the project exists, rejects
  `admin`@project, and re-assigning replaces the role (upsert); remove works.
- `delete_project` removes the project's `UserRole` rows.

**Migration:** global developer/viewer → per-project rows across existing
projects; global row dropped; admin untouched.

**Search:** filter restricts a non-admin's results to accessible projects
(may be an integration-level test depending on the search backend's
testability — note if so).

## Out of scope

- Container registry access (unchanged).
- Admin "view-as-another-user" mode.
- Project-hierarchy inheritance (parent assignment granting children).
- Non-admin (project-scoped) users managing project membership.
