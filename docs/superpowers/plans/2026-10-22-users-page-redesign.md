# Users Page Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the admin Users page into a list-and-detail layout with Users, Invitations and Roles tabs, make "Administrator" one setting that always keeps the `is_admin` flag and the global `admin` role in step, and make project access easy to edit in bulk.

**Architecture:**
- **Backend:**
  - One service function, `set_administrator`, is the only place that turns administrator on or off. It changes both halves, enforces the guards and writes an audit entry.
  - Every endpoint that touches admin calls it.
  - A new batch endpoint writes several project assignments in one transaction.
- **Frontend:**
  - The 970-line page is split into focused components under `frontend/src/components/users/`.
  - A small module of pure helpers (`access.ts`) holds the rules: what kind of user someone is, which roles can be given per project, and what "copy access" would change. The components hold no rules of their own.

**Tech Stack:**
- Backend: FastAPI, SQLAlchemy async, pytest with in-memory SQLite (`tests/_rbac.py`).
- Frontend: Next.js 16 app router, React 19, Tailwind, lucide-react, sonner toasts, the existing `useConfirm`.

**Spec:** `docs/superpowers/specs/2026-10-22-users-page-redesign-design.md`. The mockup is `docs/superpowers/specs/2026-10-22-users-page-redesign-mockup.html`.

## Global Constraints

- **Definition of Administrator:** `user.is_admin == True` **and** a `UserRole` row with `scope_type == "global"` whose role is **named** `admin`.
  - A custom role that includes the `admin` permission is not an Administrator.
- **Half set up:** exactly one of those two halves is present. The page never changes such a user unless a person clicks.
- **Roles offered per project:** never a role named `admin`, and never a role whose permissions include `"admin"`.
- **Guards** (HTTP 400, exact messages):
  - `"You cannot remove your own administrator access"`
  - `"There must be at least one active administrator"`
- **Last-admin count:** active users (`is_active`) holding the flag **or** the global `admin` role, other than the target.
- **Audit actions:** `"administrator_granted"` / `"administrator_revoked"`, with `target_type="user"`.
- **Placeholder role for members:** invite and create-user requests for a Member send the id of the system role named `viewer`. Members get no role row from those endpoints, so the id is only a placeholder.
- **UI text and colors:**
  - UI copy uses "Administrator" and "Member", never "Make admin", "Assign role" or "Global role".
  - Colors come from theme tokens (light and dark), never hard-coded hex or hsl.
- **Phone width:** the person list shows first, and choosing someone shows their details with a back control.
- **Backend tests:** run from `backend/` with `pytest` (if there is no virtualenv: `uv run --no-project --python 3.12 --with-requirements <file listing pyproject deps + dev extras> pytest`).
  - 10 tests already fail before this work, and are not this plan's concern:
    - `test_backup_api::test_the_routes_are_mounted_under_admin_backups`
    - 6 in `test_notification_events_executor.py`
    - `test_signup_status::test_it_is_mounted_with_the_other_auth_routes`
    - 2 `…mounted_under_auth_two_step`
- **Frontend checks:** there is no frontend test harness. Run `npm ci` once in `frontend/`, then `npx tsc --noEmit`, `npm run lint` and `npm run build`.

## Review Focus

1. **An administrator deletes their own global `admin` role row** through `DELETE /users/{id}/roles/{row}`. This must be refused just like "remove admin" on themselves, and not leave them flag-only. Test in Task 2.
2. **An administrator with old project rows is switched to Member.** Their project rows must survive, because `set_administrator(off)` removes only the admin halves, and they show up again. Test in Task 1.
3. **The only other administrator is deactivated.** Demoting the last *active* one must be refused. Test in Task 1.
4. **The batch request lists the same project twice, or one project does not exist.** It must be refused with 400, and nothing written. Test in Task 4.
5. **`?user=<id>` points at a deleted or unknown user** (a stale link or the back button). The page must fall back to the first person, not crash or show an empty pane. Handled in Task 7 Step 3, checked in Task 11.

---

### Task 1: `set_administrator` service

**Files:**
- Create: `backend/app/services/administrators.py`
- Test: `backend/tests/test_administrators.py`

**Interfaces:**
- Produces:
  - `class AdministratorChangeError(Exception)`. Its `str()` is the user-facing message.
  - `async def set_administrator(db: AsyncSession, user: User, on: bool, *, actor_id: uuid.UUID | None) -> None`
    - Sets `user.is_admin = on`.
    - **On:** adds the global `UserRole` for the role named `admin` if missing. If no role named `admin` exists, it sets only the flag, matching today's signup tolerance.
    - **Off:** deletes every global `UserRole` row whose role is named `admin`. Project rows and other global rows are left alone.
    - **Refuses when turning off,** raising `AdministratorChangeError`, if `actor_id == user.id`, or if no other active user holds the flag or the global admin role.
    - Writes `audit.record(action=..., actor_id=actor_id, target_type="user", target_id=user.id, metadata={"had_flag": bool, "had_role": bool}, db=db)`.
    - Flushes and does not commit.
    - Uses queries, not `user.user_roles`, so it works without eager loading.
  - `async def is_admin_role_row(db: AsyncSession, user_role: UserRole) -> bool`: True for a global row whose role is named `admin`.

- [ ] **Step 1: Write the failing tests** in `test_administrators.py`. Use the `sf` fixture pattern from `tests/test_assignment_api.py`, plus `seed_user`, `seed_role(db, "admin", ["admin"])` and `seed_project`. Each test reads the result back in a fresh session.
  - `test_turning_on_sets_flag_and_adds_global_admin_role`: afterwards `is_admin is True`, there is exactly one global row for the admin role, and an `AuditLogEntry` exists with `action == "administrator_granted"`.
  - `test_turning_on_a_role_only_user_adds_only_the_flag`: there is still exactly one global admin row, with no duplicate.
  - `test_turning_off_a_flag_only_user_clears_the_flag`: another active full admin exists, so this is allowed.
  - `test_turning_off_removes_admin_halves_but_keeps_project_rows`: the user has the flag, the global admin row and a project `viewer` row. Afterwards the flag is False, there is no global admin row, and the project row is still there.
  - `test_cannot_turn_off_yourself`: `actor_id == user.id` raises `AdministratorChangeError` with the message `"You cannot remove your own administrator access"`.
  - `test_cannot_turn_off_the_last_active_administrator`: the only other admin has `is_active=False`, so the call raises with `"There must be at least one active administrator"`.
  - `test_a_half_set_up_user_counts_as_another_administrator`: the other admin holds only the role. Turning off the target is allowed.
  - `test_turning_on_without_an_admin_role_sets_only_the_flag`: no role named admin is seeded, so the flag is True and there are no rows.

- [ ] **Step 2: Run it and check it fails.** Run `pytest tests/test_administrators.py -q`. Expected: it errors with `ModuleNotFoundError: app.services.administrators`.

- [ ] **Step 3: Implement** `administrators.py` with the interface above.

- [ ] **Step 4: Run it and check it passes.** Run `pytest tests/test_administrators.py -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/administrators.py backend/tests/test_administrators.py
git commit -m "feat(users): one function turns administrator on or off"
```

### Task 2: Endpoints that change admin go through `set_administrator`

**Files:**
- Modify: `backend/app/api/v1/users.py`:
  - `update_user`, around lines 158-194
  - `assign_role`, around lines 228-313
  - `remove_role`, around lines 316-331
- Test: `backend/tests/test_administrator_endpoints.py`

**Interfaces:**
- Consumes: `set_administrator`, `AdministratorChangeError` and `is_admin_role_row` from Task 1.
- Produces: unchanged HTTP contracts, plus these behaviours:
  - `PUT /users/{id}` with `is_admin` calls `set_administrator`.
  - `POST /users/{id}/roles` with the admin role at global scope calls `set_administrator(on=True)`. It returns the global admin row, and returns 409 if the user was already a full admin.
  - `DELETE /users/{id}/roles/{row}` on a global admin row calls `set_administrator(on=False)`.
  - `AdministratorChangeError` is turned into `HTTPException(400, detail=str(e))`.
  - `assign_role` and `remove_role` now need `current_user` (rename `_current_user`). Update `tests/test_assignment_api.py`, which passes `_current_user=None`, to `current_user=None`.

- [ ] **Step 1: Write the failing tests.** Call the route functions directly, as `test_assignment_api.py` does, passing a `User` object loaded from the db as `current_user`.
  - `test_put_is_admin_true_adds_the_role`
  - `test_put_is_admin_false_removes_the_role_too`: start from a full admin, with another full admin acting.
  - `test_assigning_the_admin_role_globally_sets_the_flag`
  - `test_removing_the_global_admin_row_clears_the_flag`
  - `test_removing_your_own_global_admin_row_is_refused`: the acting user deletes their own admin row. Expect `HTTPException` with status 400 and detail `"You cannot remove your own administrator access"`. The row still exists and the flag is still True.
  - `test_put_is_admin_false_on_the_last_administrator_is_refused`: 400.
  - `test_removing_a_project_row_does_not_touch_admin`: a project viewer row is removed, and the flag is unchanged.

- [ ] **Step 2: Run them and check they fail.** Run `pytest tests/test_administrator_endpoints.py -q`. Expected: the assertions about the second half fail; for example, the flag is still False after the role is assigned.

- [ ] **Step 3: Implement the routing in `users.py`.**
  - The old flag-only branch in `update_user` and its self-check are replaced by `set_administrator`.
  - Keep the "cannot deactivate your own account" check as it is.

- [ ] **Step 4: Run the tests.** Run `pytest tests/test_administrator_endpoints.py tests/test_assignment_api.py tests/test_user_creation_transition.py tests/test_permission_enforcement.py -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/users.py backend/tests/test_administrator_endpoints.py backend/tests/test_assignment_api.py
git commit -m "feat(users): the admin setting and the admin role change together"
```

### Task 3: Creation paths and restore leave both halves set

**Files:**
- Modify:
  - `backend/app/api/v1/users.py` `create_user` (lines 91-137)
  - `backend/app/api/v1/invites.py` accept (lines 162-174)
  - `backend/app/api/v1/auth.py` signup (lines 70-85)
  - `backend/app/services/seed.py` (lines 49-64)
  - `backend/app/services/backup/restore.py` `_keep_acting_admin` (lines 95-124)
- Test: add to `backend/tests/test_user_creation_transition.py` and `backend/tests/test_backup_restore.py`.

**Interfaces:**
- Consumes: `set_administrator(db, user, True, actor_id=...)` from Task 1.
  - `actor_id` is the acting admin in `create_user`, and None in invite accept, signup and seed.
- Produces:
  - The behaviour is the same as today for those four paths, except that each now writes an audit entry.
  - In restore, the restored rows always give the restoring account a global `admin` role row, besides `is_admin=True`. To do this, find the role row named `admin` in `rows["roles"]` and append a `user_roles` row dict (new `id`, `user_id=account id`, `role_id`, `scope_type="global"`, `scope_id=None`, plus `created_at` if the column needs it) when none exists. If the backup has no admin role, leave the rows unchanged.
  - Restore works on row dicts, so it does not call `set_administrator`.

- [ ] **Step 1: Write the failing tests.**
  - `test_create_admin_user_writes_an_audit_entry`: `create_user` with the admin role leaves an `AuditLogEntry` with action `administrator_granted`.
  - `test_restore_gives_the_restoring_account_the_admin_role`: in `test_backup_restore.py`, using the existing `_seed`, `_export` and `_restore` helpers. Remove the acting admin's global admin row from the payload before restoring. Afterwards the acting account has `is_admin` True **and** a global row for the role named `admin`.

- [ ] **Step 2: Run them and check they fail.** Run `pytest tests/test_user_creation_transition.py tests/test_backup_restore.py -q`. Expected: the 2 new tests fail and the others pass.

- [ ] **Step 3: Implement.** The four creation paths call `set_administrator` instead of setting `is_admin` and adding the row by hand. In signup, only the first user goes through it; later signups keep the `MEGOOCI_DEFAULT_ROLE` row exactly as today. Restore appends the role row as described above.

- [ ] **Step 4: Run the tests.** Run `pytest tests/test_user_creation_transition.py tests/test_backup_restore.py tests/test_signup_status.py tests/test_two_step_backup.py -q`. Expected: all pass, except the known `test_it_is_mounted_with_the_other_auth_routes`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/users.py backend/app/api/v1/invites.py backend/app/api/v1/auth.py backend/app/services/seed.py backend/app/services/backup/restore.py backend/tests/test_user_creation_transition.py backend/tests/test_backup_restore.py
git commit -m "feat(users): every way of making an administrator sets the setting and the role"
```

### Task 4: Batch project assignment endpoint

**Files:**
- Modify:
  - `backend/app/schemas/roles.py` (add schemas)
  - `backend/app/api/v1/users.py` (add route)
  - `frontend/src/lib/api.ts` (client)
- Test: `backend/tests/test_project_roles_batch.py`

**Interfaces:**
- Produces:
  - **Schemas:**
    - `class ProjectRoleAssignment(BaseModel): project_id: uuid.UUID; role_id: uuid.UUID`
    - `class ProjectRolesBatch(BaseModel): assignments: list[ProjectRoleAssignment] = Field(min_length=1, max_length=500)`
  - **Route:** `POST /users/{user_id}/project-roles`, which needs `users.manage`. In Python it is `assign_project_roles(user_id, body, db, current_user) -> list[UserRoleResponse]`.
    - It returns all of the user's project-scoped rows after the write.
    - It validates everything before writing anything:
      - 404 `"User not found"`
      - 400 `"Each project may appear only once"`
      - 404 `"Role not found"`
      - 400 `"The admin role cannot be scoped to a project"`
      - 400 `"scope_id must reference an existing project"`
    - Then it upserts one row per (user, project), with the same replace-in-place rule as `assign_role`.
  - **Client:** `usersApi.assignProjectRoles(userId: string, assignments: { project_id: string; role_id: string }[]): Promise<UserRoleAssignment[]>`

- [ ] **Step 1: Write the failing tests.**
  - `test_batch_adds_several_projects`
  - `test_batch_replaces_an_existing_role_on_a_project`
  - `test_batch_with_an_unknown_project_writes_nothing`: one good project and one random UUID. Expect 400, and no rows for the good project.
  - `test_batch_with_a_duplicate_project_is_refused`: 400, and nothing is written.
  - `test_batch_refuses_the_admin_role`: 400.

- [ ] **Step 2: Run them and check they fail.** Run `pytest tests/test_project_roles_batch.py -q`. Expected: an `ImportError` for `assign_project_roles`.

- [ ] **Step 3: Implement** the schemas, the route and the client function.

- [ ] **Step 4: Run them and check they pass.** Run `pytest tests/test_project_roles_batch.py -q`. Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/roles.py backend/app/api/v1/users.py backend/tests/test_project_roles_batch.py frontend/src/lib/api.ts
git commit -m "feat(users): add several projects to a person in one request"
```

### Task 5: Access rules module (frontend)

**Files:**
- Create: `frontend/src/components/users/access.ts`

**Interfaces:**
- Consumes: `UserDetail`, `UserRoleInfo` and `Role` from `@/lib/api`.
- Produces (all pure, with no React):
  - `type AccessKind = "administrator" | "member" | "half"`
  - `accessKind(user: UserDetail): AccessKind`: uses the definitions in Global Constraints.
  - `missingAdminHalf(user: UserDetail): "flag" | "role" | null`
  - `isAssignablePerProject(role: Role): boolean`: false when the role is named `admin` or its permissions include `"admin"`.
  - `projectRows(user: UserDetail): UserRoleInfo[]`: the project-scoped rows, sorted by `project_name` with `localeCompare(..., undefined, { sensitivity: "base" })`.
  - `leftoverGlobalRoles(user: UserDetail, roles: Role[]): { row: UserRoleInfo; grantsAdmin: boolean }[]`: the global rows whose role is not named `admin`. `grantsAdmin` is true when that role's permissions include `"admin"`.
  - `copyPreview(target: UserDetail, source: UserDetail, roles: Role[]): { adds: Assignment[]; changes: Assignment[] }`, where `type Assignment = { project_id: string; project_name: string; role_id: string; role_name: string }`. It skips the source's rows whose role is not assignable per project, and rows the target already has with the same role.
  - `memberRoleId(roles: Role[]): string | undefined` returns the id of the role named `viewer`. `adminRoleId(roles: Role[])` returns the id of the role named `admin`.

- [ ] **Step 1: Write the module.** It is pure and has no tests (there is no harness), so keep each function a few lines long and give each a one-line comment that cites the rule from Global Constraints.

- [ ] **Step 2: Type-check.** Run `cd frontend && npm ci && npx tsc --noEmit`. Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/users/access.ts
git commit -m "feat(ui): the users page's access rules in one module"
```

### Task 6: Page shell with tabs; Invitations and Roles tabs

**Files:**
- Modify: `frontend/src/app/admin/users/page.tsx`. It becomes the shell: header, actions, tabs and dialogs.
- Create:
  - `frontend/src/components/users/InvitationsTab.tsx`
  - `frontend/src/components/users/RolesTab.tsx`
  - `frontend/src/components/users/AccessKindCards.tsx`

**Interfaces:**
- Consumes: `memberRoleId` and `adminRoleId` from Task 5.
- Produces:
  - `<AccessKindCards value={"administrator" | "member" | null} onChange={(v) => void} disabled?={boolean} />`: the two radio cards, with the copy from the spec. Tasks 8 and 10 use it.
  - `<InvitationsTab invites={Invite[]} loading={boolean} onChanged={() => void} />`
  - `<RolesTab roles={Role[]} loading={boolean} />`
  - **Tab state:** the page reads and writes `?tab=users|invitations|roles` (default `users`) with `useSearchParams` and `router.replace`. Wrap the page body in `React.Suspense` as `app/artifacts/page.tsx` does.

- [ ] **Step 1: Build the shell.**
  - Title "Users", description "Manage who can sign in and what they can reach.", and the existing Create user and Invite member buttons.
  - Tabs: "Users" with a count, "Invitations" with a pending count, and "Roles".
  - The Users tab renders the existing user list for now. Task 7 replaces it.

- [ ] **Step 2: Build `InvitationsTab`.**
  - Move the current invitations card content into it.
  - The row label becomes "Administrator" when `role_name === "admin"`, and "Member" otherwise.

- [ ] **Step 3: Change the invite dialog** to use `AccessKindCards` in place of the role `Select`. It sends `adminRoleId(roles)` for Administrator and `memberRoleId(roles)` for Member. Add a comment saying the member role id is a placeholder.

- [ ] **Step 4: Build `RolesTab`.**
  - It is a table with one row per permission and one column per role, ordered: `admin` labelled "Administrator", then other system roles, then custom roles.
  - Rows come from a `PERMISSION_SENTENCES: Record<string, { area: string; sentence: string }>` map in the same file. It covers every permission in `backend/app/core/permissions.py` `VALID_PERMISSIONS`, grouped into the areas "Projects & pipelines", "Builds & artifacts", "Secrets" and "Server".
  - A permission missing from the map is shown under "Other" with its raw name.
  - Each sentence has the raw name as its `title`.

- [ ] **Step 5: Check.** Run `npx tsc --noEmit && npm run lint`. Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/app/admin/users/page.tsx frontend/src/components/users/
git commit -m "feat(ui): users page tabs, invitations as Administrator or Member, roles as a table"
```

### Task 7: Users tab: list and detail

**Files:**
- Create:
  - `frontend/src/components/users/UsersTab.tsx`
  - `frontend/src/components/users/UserList.tsx`
  - `frontend/src/components/users/UserDetailPane.tsx`
- Modify: `frontend/src/app/admin/users/page.tsx` (render `UsersTab`; delete the old user cards, the "Assign role" dialog and its handlers)

**Interfaces:**
- Consumes: `accessKind`, `missingAdminHalf`, `projectRows` and `leftoverGlobalRoles` from Task 5.
- Produces:
  - `<UsersTab users={UserDetail[]} roles={Role[]} loading={boolean} onChanged={() => void} />`
  - **Selection state:** `?user=<id>`. When the id is missing or unknown, the first person in the list is selected.
  - `<UserDetailPane user={UserDetail} roles={Role[]} users={UserDetail[]} onChanged={() => void} />`, with detail tabs Access, Security and Account. Access is a placeholder until Task 8.

- [ ] **Step 1: Build `UserList`.**
  - A search box filters by name or email, case-insensitive.
  - Groups, in this order: "Needs a decision · N" (only when N > 0), "Administrators · N", "Members · N". People are sorted by name inside each group.
  - Each entry shows initials, the name and a meta line:
    - "Every project" for administrators.
    - "N projects" for members, plus " · N as developer" when there are any.
    - "Admin access half set up" for the half state.
    - Amber suffixes, using theme tokens: "two-step off", and "sees all projects" when `leftoverGlobalRoles` is not empty.
  - Inactive people are dimmed.
  - An empty search result shows "No one matches".

- [ ] **Step 2: Build `UserDetailPane`'s header and the Security and Account tabs.**
  - The header shows initials, name, email, a two-step badge, and "Inactive" when the user is inactive.
  - **Security:** two-step status, plus the existing reset action and confirmation. It is disabled when two-step is off or when this is you, with a reason shown next to it. The Security tab label gets an amber dot when two-step is off.
  - **Account:** "Member since <date>", Active/Inactive with Deactivate or Activate, and "Delete user…". These keep the existing confirmations and are disabled for yourself.

- [ ] **Step 3: Build `UsersTab`.**
  - It shows the list and detail side by side at the `md` breakpoint and wider.
  - Below `md`, it shows the list or the detail, with a "← All users" back control that clears `?user`.
  - It handles a stale `?user` id with the fallback described above.

- [ ] **Step 4: Check.** Run `npx tsc --noEmit && npm run lint`. Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/app/admin/users/page.tsx frontend/src/components/users/
git commit -m "feat(ui): users as a list beside the selected person's details"
```

### Task 8: Access tab: level, half-set-up decision, leftover role warning

**Files:**
- Create: `frontend/src/components/users/AccessTab.tsx`
- Modify: `frontend/src/components/users/UserDetailPane.tsx` (render it)

**Interfaces:**
- Consumes:
  - `AccessKindCards` from Task 6.
  - `accessKind`, `missingAdminHalf` and `leftoverGlobalRoles` from Task 5.
  - `usersApi.update(id, { is_admin })` and `usersApi.removeRole`.
- Produces: `<AccessTab user roles users onChanged />`. It renders `<ProjectAccessEditor>` from Task 9 for members. Until Task 9 lands, it shows the project count.

- [ ] **Step 1: Build the access level.**
  - `AccessKindCards`, with `value` set from `accessKind`. The value is `null` for half-set-up users.
  - Changing it asks `useConfirm` (warning tone), then calls `usersApi.update(user.id, { is_admin })`.
  - A server 400 shows its `detail` in an error toast.
  - On your own account, the cards are disabled, with the note "You can't remove administrator from your own account".

- [ ] **Step 2: Build the half-set-up notice.**
  - The notice text, where `<name>` is the user's name:
    - Missing flag: "<name> has the administrator role but not the administrator setting. The server treats them as an administrator, but the admin pages are hidden from them."
    - Missing role: "<name> has the administrator setting but not the administrator role. The server treats them as an administrator."
  - Buttons **Make administrator** and **Make member**. Each confirms, then calls `usersApi.update(user.id, { is_admin: true | false })`.

- [ ] **Step 3: Build the leftover global role warning**, for members only.
  - Normal text: "<name> can see every project. They still have the old global <role> role, which overrides the N projects listed."
  - When `grantsAdmin`: "<name> has full administrator access through the custom role <role>."
  - A button **Remove global <role>** calls `usersApi.removeRole(user.id, row.id)`.

- [ ] **Step 4: Check.** Run `npx tsc --noEmit && npm run lint`. Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/users/
git commit -m "feat(ui): one Administrator or Member choice, and decisions for half-set-up admins"
```

### Task 9: Project access editor

**Files:**
- Create:
  - `frontend/src/components/users/ProjectAccessEditor.tsx`
  - `frontend/src/components/users/ProjectMultiSelect.tsx`
  - `frontend/src/components/users/CopyAccessDialog.tsx`
- Modify: `frontend/src/components/users/AccessTab.tsx` (render the editor for members)

**Interfaces:**
- Consumes:
  - `projectRows`, `isAssignablePerProject` and `copyPreview` from Task 5.
  - `usersApi.assignProjectRoles` from Task 4, `usersApi.assignRole` for single-row role changes, `usersApi.removeRole`, and `projectsApi.listAll()`.
- Produces:
  - `<ProjectAccessEditor user={UserDetail} roles={Role[]} users={UserDetail[]} onChanged={() => void} />`. Task 10 reuses it in the create-user dialog.
  - `<ProjectMultiSelect projects={Project[]} exclude={Set<string>} value={string[]} onChange={(ids: string[]) => void} />`: a searchable checkbox list built from the existing `Input`, `ScrollArea` and native checkboxes.

- [ ] **Step 1: Build the list.**
  - The heading reads "Projects · N", with a filter input and a "Copy access from…" button.
  - Rows come from `projectRows`. They are laid out in 2 columns at `lg` and 1 column below that.
  - Each row shows the project name, a `Select` of the assignable roles and a remove button.
  - A role change calls `assignRole(user.id, { role_id, scope_type: "project", scope_id })` (an upsert) and shows the toast "Role changed".
  - Remove calls `removeRole`, with no confirmation, and shows the toast "Removed from <project>".
  - Developer and viewer rows use the color treatment from the mockup, through theme tokens; custom roles use a neutral style.
  - A member with no projects sees "No projects yet. Add some below."

- [ ] **Step 2: Build the add row.**
  - It has `ProjectMultiSelect` (excluding projects the user already has), a role `Select` defaulting to `viewer`, and an **Add** button.
  - Add calls `assignProjectRoles` and toasts "Added N projects".

- [ ] **Step 3: Build `CopyAccessDialog`.**
  - First pick another member. The dialog then shows `copyPreview` as "Adds N projects, changes N roles", with the lists expandable.
  - Confirming sends `adds + changes` through `assignProjectRoles`.
  - When there is nothing to copy, it shows "Nothing to copy: <name> already has all of it." and disables confirm.

- [ ] **Step 4: Check.** Run `npx tsc --noEmit && npm run lint`. Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/users/
git commit -m "feat(ui): edit project access in place, add several projects, copy access"
```

### Task 10: Create user dialog, and removing the old editor

**Files:**
- Modify: `frontend/src/app/admin/users/page.tsx` (create dialog)
- Delete: `frontend/src/components/ProjectAssignmentsEditor.tsx`
- Test: `backend/tests/test_users_page_source.py`

**Interfaces:**
- Consumes:
  - `AccessKindCards` from Task 6.
  - `ProjectAccessEditor` from Task 9.
  - `adminRoleId` and `memberRoleId` from Task 5.

- [ ] **Step 1: Write the failing source test.** Read the files the way `test_notification_events_docs_panel.py` does, and skip if the frontend is absent.
  - `test_the_users_page_has_no_global_role_assignment`: `page.tsx` contains none of `"Assign role"`, `"openAssignRole"`, `"Initial role"` or `"Make admin"`.
  - `test_the_old_project_editor_is_gone`: `frontend/src/components/ProjectAssignmentsEditor.tsx` does not exist, and no file under `frontend/src` imports `ProjectAssignmentsEditor`.
  - `test_invitations_show_administrator_or_member`: `InvitationsTab.tsx` contains `"Administrator"` and `"Member"`, and does not contain `"Role:"`.
  - `test_project_roles_offered_exclude_admin_permission`: `access.ts` defines `isAssignablePerProject`, and `ProjectAccessEditor.tsx` uses it.

- [ ] **Step 2: Run it and check it fails.** Run `pytest tests/test_users_page_source.py -q`. Expected: the first two tests fail.

- [ ] **Step 3: Change the create dialog.**
  - Use `AccessKindCards` and remove "Initial role".
  - It sends `adminRoleId(roles)` for Administrator and `memberRoleId(roles)` for Member. Remove the follow-up `usersApi.update(..., { is_admin: true })`.
  - The success view for a member renders `ProjectAccessEditor`.
  - Delete `ProjectAssignmentsEditor.tsx`.

- [ ] **Step 4: Run the tests.** Run `pytest tests/test_users_page_source.py -q && cd ../frontend && npx tsc --noEmit && npm run lint`. Expected: all pass and the checks are clean.

- [ ] **Step 5: Commit**

```bash
git add -A frontend/src backend/tests/test_users_page_source.py
git commit -m "feat(ui): create users as Administrator or Member; retire the old project editor"
```

### Task 11: Whole-change verification

**Files:** none, unless a check fails.

- [ ] **Step 1: Run the backend suite.** Run `cd backend && pytest -q`. Expected: only the 10 known failures from Global Constraints.

- [ ] **Step 2: Run the frontend build.** Run `cd frontend && npx tsc --noEmit && npm run lint && npm run build`. Expected: all succeed.

- [ ] **Step 3: Check by hand in a browser** (dev stack: `make dev`). Check light and dark themes, and widths of 1280px and 390px.
  - [ ] Seven users render. The groups and meta lines match the spec.
  - [ ] Switching a member to Administrator and back works, and keeps their project rows.
  - [ ] Demoting yourself is refused with the exact message.
  - [ ] Make a user half set up through the API, once flag-only and once role-only. They appear under "Needs a decision", and both buttons resolve them.
  - [ ] A member given a global viewer row through the API shows the warning. Removing it works.
  - [ ] Add three projects at once. Change a role. Remove a project. Copy access from another member, with a correct preview.
  - [ ] `?user=<random uuid>` falls back to the first person. `?tab=roles` opens the Roles tab.
  - [ ] Invite and Create user both offer Administrator and Member. A created Administrator has the setting and the role.

- [ ] **Step 4: Commit any fixes**, using a message that names what was fixed.
