# Users page redesign

Date: 2026-10-22
Status: draft for review
Mockup: [2026-10-22-users-page-redesign-mockup.html](2026-10-22-users-page-redesign-mockup.html). Open it in a browser. It shows the agreed layout with real user and project names.

## Why

The admin Users page (`frontend/src/app/admin/users/page.tsx`) is hard to use for its main job: deciding who can reach which project, and with which role.

- Every user is a card. The card lists each project assignment as a removable chip, and has its own "Select project / Role / Assign" form. A member with 17 projects becomes a wall of chips, and seven users already fill several screens.
- Invitations, with an empty box when there are none, and the Roles reference, which is rows of raw permission chips, sit below the user list on the same long page.
- "Admin" means two different things, and the page exposes both:
  - "Make admin" flips the `users.is_admin` flag.
  - "Assign role → admin" adds a global `admin` role row.

  Admins find this confusing, and it causes real drift (next section).

Success looks like this: an admin can open the page, find a person, see their access at a glance, and add, change or remove project access in a few clicks, without scrolling past other people. "Admin" means one thing.

## What the code does today with "admin" (investigation summary)

- **The backend treats the two as equivalent almost everywhere.** `effective_permissions` folds both into the `"admin"` sentinel (`backend/app/core/deps.py:58-79`), and `require_permission`, `get_current_admin_user`, `has_global_permission`, project access, MCP and search all accept it.
- **Some things read only the flag.**
  - Admin notifications: `get_admin_user_ids` (`services/in_app_notifications.py:103`) feeds agent-offline alerts, backup-failed alerts and the approval-gate fallback.
  - The registry fast path (`core/registry_auth.py:137`).
  - The builds WebSocket (`api/v1/websocket.py:89`).
  - The AI assistant secrets check (`api/v1/ai_assistant.py:967`).
  - `role` in `/me`.
  - The frontend decides all admin UI from `is_admin`: the sidebar entries Users, Integrations and Backups, `RequireAdmin`, and the settings Maintenance and Security tabs.
- **The two are set together only at creation:** first signup, env seed, `POST /users` with the admin role, and invite accept with the admin role.
- **After creation they drift apart:**
  - `PUT /users/{id}` with `is_admin` changes only the flag.
  - `POST` / `DELETE /users/{id}/roles` change only the role row.
  - Restore forces only the flag (`services/backup/restore.py:119`).
  - The UI produces both drift states:
    - "Assign role → admin" gives the role without the flag. That person is a full admin through the API, MCP and tokens, but the admin pages are hidden from them.
    - "Remove admin" on such a user leaves the role, so they are still a full admin to the server.
    - "Create user → Admin" gives the flag without the role.
- **Global non-admin roles still exist.** Signups get `MEGOOCI_DEFAULT_ROLE` globally, and the "Assign role" dialog assigns globally. A global `viewer` or `developer` role grants that role on **every** project (`core/access.py:45-53`), whatever the person's project list says.

## Goals

1. **Split users page.** The Users page becomes a list of people next to the selected person's details, as in mockup section 1.
2. **Tabs.** Users, Invitations and Roles become separate tabs on the page.
3. **One "Administrator" concept, kept consistent everywhere.** An administrator is a user with `is_admin = true` **and** a global `admin` role row. Every way of granting or removing admin changes both. Existing users who hold only one half are flagged on the page for an administrator to decide; they are not changed automatically.
4. **Project access editing that scales.**
   - The list shows one row per project with a role selector.
   - Add several projects at once with one role.
   - Copy another person's project access.
   - Filter the list.
5. **Visible warning for a member who has a leftover global non-admin role**, with a one-click removal.
6. **Clearer secondary surfaces.**
   - Security and Account actions get their own tabs.
   - The Create user and Invite dialogs ask "Administrator or Member" instead of offering a role that does nothing for members.
   - The Roles tab shows permissions as a grouped table with ticks.

## Non-goals

- **The two access-control holes found during the investigation.** They are a separate change:
  - Anyone with `users.manage` can set `is_admin` on any account, including their own.
  - A custom role that includes the `admin` permission can be assigned at project scope, and it then passes the global `require_permission` and `get_current_admin_user` gates.
- **Changing what self-signup grants** (`MEGOOCI_DEFAULT_ROLE` stays a global role). Those users will now show the warning from goal 5.
- **Giving invitations project access up front.** Invited members still get project access after they join.
- **Removing global non-admin roles from the backend or the API.** Only the UI stops creating them.
- **Grouping projects by environment.** Projects have no environment field; "Staging" and "Production" are only part of the name. The list is alphabetical by name, which happens to put "X Production" and "X Staging" next to each other.
- **The project page's Members panel** (`ProjectMembersPanel.tsx`) and the access-grid view (option C from brainstorming). These are possible follow-ups.
- **Changes to the `is_admin` consumers listed above.** Once flag and role always match, they behave correctly as they are.

## Anti-goals (would make this a failure even if it works)

- Anyone gains or loses admin without an administrator choosing it on the page.
- The page again offers two different ways to make someone an admin.
- A person's real access differs from what the page shows. For example, a global viewer role must not be hidden behind a project list that looks limited.

## Constraints

- **Stack and component kit.** Use the existing stack: Next.js app router, the shadcn-style components in `frontend/src/components/ui`, `lucide-react` icons and Tailwind. Use the existing API client (`frontend/src/lib/api.ts`) and the toast and `useConfirm` patterns already used on the page.
- **Theme.** The app has light and dark themes, so colors come from theme tokens, not the mockup's hard-coded values.
- **Frontend tests.** There is no frontend test harness. The backend is tested with pytest (`backend/tests`), and frontend text that matters can be checked from source the way `test_notification_events_docs_panel.py` does.
- **Who can use the page.** The page stays admin-only (`RequireAdmin`).
- **Phone width.** The page must work at phone width: there, the person list is shown first, and choosing someone opens their details with a back control.

## The design

### Page shell

- **Header.** Title "Users" with a one-line description, plus the existing actions "Create user" and "Invite member".
- **Tabs.**
  - The tabs are **Users** (with a count), **Invitations** (with a pending count) and **Roles**.
  - The active tab is kept in the URL (`?tab=`), so reloads and links keep it.

### Users tab: list and detail

- **Left column: the person list.**
  - A search box at the top filters by name or email.
  - People are grouped under **Administrators · N** and **Members · N**, sorted by name inside each group.
  - Each entry shows an avatar with initials, the name, and a meta line:
    - Administrators: "Every project".
    - Members: "N projects", plus "· N as developer" when any project has the developer role.
    - Amber "two-step off" is added for anyone without two-step verification.
    - Amber "sees all projects" is added for a member with a leftover global role.
  - Administrators' avatars have the primary-color ring.
- **Right column: the selected person.**
  - The selection is kept in the URL (`?user=<id>`). With no selection, the first person is shown.
  - The header shows the avatar, name, email, and a two-step badge (green "Two-step on" or amber "Two-step off").
  - Inactive users show an "Inactive" badge in the header, and their list entry is dimmed.
- **Detail tabs.** **Access**, **Security** and **Account**. The Security tab carries a small amber dot when two-step is off.

### Access tab

- **Access level.** Two radio cards: **Administrator** ("Everything, on every project, including users, integrations, backups and settings") and **Member** ("Only the projects below, with the role given on each").
  - Switching asks for confirmation first, using the existing `useConfirm` with a warning tone.
  - Switching to Administrator sets the flag and adds the global `admin` role. The person's project rows are kept but hidden while they are an administrator.
  - Switching to Member removes both. Member is refused for your own account and for the last administrator; see the backend section.
- **Projects** (Members only).
  - The heading shows the count, a filter box and a "Copy access from…" button.
  - The rows are alphabetical by project name, in two columns on wide screens and one on narrow ones.
  - Each row shows the project name, an inline role selector (only non-admin roles: developer, viewer and any custom non-admin role), and a remove control.
  - Changing the selector saves immediately (the existing upsert) and shows a toast.
  - Remove deletes the row without a confirmation dialog and shows a toast. Re-adding is one step, so a confirmation would only slow the admin down.
  - Developer and viewer are color-coded as in the mockup. Custom roles use a neutral style.
- **Add row.**
  - A searchable multi-select of projects the person does not already have, a role selector (default viewer), and an **Add** button.
  - Adding calls the batch endpoint below, so either all the chosen projects are added or none are.
- **Copy access from…**
  - Pick another member. The dialog previews what will be added or changed ("Adds 6 projects, changes 2 roles") and needs a confirm.
  - Copy adds or overwrites assignments from the source person. It never removes the target's other projects.
- **Leftover global role warning.**
  - Shown when a member has any global role row other than `admin`.
  - The text: "<name> can see every project. They still have the old global <role> role, which overrides the N projects listed."
  - If that global role is a custom role that includes the `admin` permission, the text says so instead: "<name> has full administrator access through the custom role <role>." (my call)
  - A **Remove global <role>** button removes it, using the existing `DELETE /users/{id}/roles/{user_role_id}`.
- **Roles offered per project.** The role selector and the Add row never offer a role that includes the `admin` permission, so this page cannot create the project-scope hole listed under non-goals. (my call)
- **Administrators.** Their Access tab shows only the access-level cards, with Administrator selected, and the note "You can't remove administrator from your own account" when viewing yourself.

### Half-set-up administrators

A user who has the `is_admin` flag without the global `admin` role, or the role without the flag, is "half set up". The server already treats them as a full administrator.

- **In the list** they get their own group at the top, **Needs a decision · N**, which is only shown when N > 0. Their meta line says "Admin access half set up" in amber.
- **In their Access tab:**
  - Neither access-level card is selected.
  - A notice explains the state in plain words. For example: "Mourad Khalsi has the administrator role but not the administrator setting. The server treats them as an administrator, but the admin pages are hidden from them." The text names the half that is missing.
  - Two buttons follow: **Make administrator** and **Make member**. Each asks for confirmation and calls `set_administrator` on or off, through `PUT /users/{id}` with `is_admin`, which completes or clears both halves.
- Once every account is decided, the group and the notices disappear. Nothing changes without a person choosing.

### Security tab

- Two-step status: on or off.
- **Reset two-step verification**, the existing action and confirmation. It is disabled when two-step is off or when viewing yourself, with a short reason next to it.

### Account tab

- Status: Active or Inactive, with **Deactivate** / **Activate**. These are the existing actions, disabled for yourself.
- **Delete user…**: the existing destructive confirmation, disabled for yourself.
- "Member since <created_at>".

### Invitations tab

- The same content as today's Invitations card: the list, Resend, Revoke and Clear history, moved into the tab.
- Each row shows **Administrator** or **Member** instead of "Role: <role_name>". The label is derived from `role_name == "admin"`.
- **Invite dialog.** It asks Administrator or Member, using the same two cards, instead of a role dropdown.
  - The API still needs a `role_id`. Administrator sends the `admin` role id. Member sends the `viewer` system role id, and members get no role row on accept (`invites.py:175`), so that id is only a placeholder.
  - The placeholder behaviour is noted in a code comment.

### Create user dialog

- Administrator or Member, name and email. The "Initial role" dropdown is removed, because members get no role row from `POST /users`.
- **Administrator** sends the `admin` role id. The backend already sets both the flag and the role then (`users.py:117-124`). This replaces today's "create as member, then PUT `is_admin`", which leaves an admin without the role.
- **Member** sends the `viewer` role id as the placeholder (as above).
- After a member is created, the success view keeps offering project access, now through the new project access editor component.

### Roles tab

- A table with one row per permission and one column per role: Administrator, then the other system roles, then custom roles.
- Rows are grouped by area: Projects & pipelines, Builds & artifacts, Secrets, Server (users, invitations, integrations, agents, registry, backups, settings, git connections).
- Each permission is shown as a plain sentence. The raw name (e.g. `pipelines.manage`) appears as a tooltip.
- A permission with no sentence in the map falls back to its raw name, so new permissions still show up.
- A tick means the role has the permission.

### Backend changes

0. **Definition used everywhere below.** "Administrator" means the role **named** `admin` together with the flag. A custom role that includes the `admin` permission does not count: it does not make someone an Administrator on this page, it does not trigger `set_administrator`, and it is not counted by the last-administrator guard. It does get the warning described under the Access tab. (my call; closing that hole on the server is in the non-goals)
1. **One function sets administrator.** For example, `set_administrator(db, user, on: bool)` in `app/services/` or next to the users router. It sets `user.is_admin` and adds or removes the global `admin` `UserRole` row in the same transaction.
   - It is used by:
     - `PUT /users/{id}` when `is_admin` is in the body.
     - `POST /users/{id}/roles` when the role is `admin` at global scope (this also sets the flag).
     - `DELETE /users/{id}/roles/{id}` when the row is the global `admin` role (this also clears the flag).
     - Restore (`restore.py:119`).
   - Creation paths already set both. They should call the same function, so there is only one way.
2. **Guards.** Turning administrator off is refused, with 400 and a clear message, for:
   - **your own account.** This exists for the flag today. Extend it to the role path.
   - **the last active administrator.** This guard is new. It counts active users the server treats as admin, which means those with the flag or the global `admin` role, other than the target. So making a half-set-up user a member can never leave the server with no administrator. (my call)
3. **Batch project assignment.** For example, `POST /users/{id}/project-roles` with `{"assignments": [{"project_id": ..., "role_id": ...}, ...]}`.
   - It upserts every assignment in one transaction, using the same checks as the single-assignment path: the project exists, and the role is not `admin` at project scope.
   - It returns the user's project roles.
   - Both "Add projects" and "Copy access from…" use it.
4. **No automatic reconciliation.** Users who already hold only the flag or only the global `admin` role are not changed by a migration. The page marks them, and an administrator decides for each one (see "Half-set-up administrators" below). This was decided with the product owner, because nothing records whether the drift came from a promotion or from a demotion.
5. **Audit.** Every `set_administrator` call writes an audit entry (`app/core/audit.py`) with the actor, the target user, and whether administrator was turned on or off. (my call)

### Testing

Backend tests in pytest:

- **`set_administrator`:**
  - Every endpoint path above keeps the flag and the role row in agreement.
  - The self guard and the last-admin guard work.
  - Restore leaves the restoring account as a full administrator, with the role row.
  - Turning administrator on or off for a half-set-up user (flag only, role only) leaves both halves matching.
  - The last-admin guard counts half-set-up users.
  - Each call writes an audit entry.
- **Batch endpoint:**
  - It is atomic: one bad project id means nothing is written.
  - It upserts.
  - It refuses `admin` at project scope.
  - It needs `users.manage`.

Frontend:

- A source-reading test, in the style of `test_notification_events_docs_panel.py`, checks that the users page no longer contains an "Assign role" action or a global role dropdown.
- Manual check:
  - in a browser at desktop and phone widths, in light and dark themes, with the seven current users
  - with a user holding a leftover global viewer role
  - with users in each half-set-up state

## Left to the builder

- How to split the page into components. Suggested: `UsersTab`, `UserList`, `UserDetail`, `ProjectAccessEditor` replacing `ProjectAssignmentsEditor`, `InvitationsTab`, `RolesTab`.
- The exact plain-language sentence for each permission.
- The empty states: no users matching the search, a member with no projects, no invitations.
- The loading skeletons, the multi-select component (build one from existing parts if the kit has none), and the exact breakpoints.
