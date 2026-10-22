/**
 * The rules the users page shows and edits, kept apart from the components.
 *
 * An administrator holds both the `is_admin` setting and a global row for the
 * role named `admin`. A user with only one of the two is "half" set up: the
 * server already treats them as an administrator, and a person decides which
 * way to settle it.
 */
import type { Role, UserDetail, UserRoleInfo } from "@/lib/api";

export type AccessKind = "administrator" | "member" | "half";

export interface Assignment {
  project_id: string;
  project_name: string;
  role_id: string;
  role_name: string;
}

const ADMIN = "admin";

// A global row for the role *named* admin; a custom role with the admin
// permission does not make someone an administrator.
function hasAdminRole(user: UserDetail): boolean {
  return user.roles.some((r) => r.scope_type === "global" && r.role_name === ADMIN);
}

export function accessKind(user: UserDetail): AccessKind {
  const role = hasAdminRole(user);
  if (user.is_admin && role) return "administrator";
  if (user.is_admin || role) return "half";
  return "member";
}

/** For a half-set-up user, the half they are missing. */
export function missingAdminHalf(user: UserDetail): "flag" | "role" | null {
  const role = hasAdminRole(user);
  if (user.is_admin && !role) return "role";
  if (!user.is_admin && role) return "flag";
  return null;
}

// Never the admin role, and never a role carrying the admin permission:
// either would make a per-project grant a server-wide one.
export function isAssignablePerProject(role: Role): boolean {
  return role.name !== ADMIN && !role.permissions.includes(ADMIN);
}

/** Project rows, alphabetical by project name. */
export function projectRows(user: UserDetail): UserRoleInfo[] {
  return user.roles
    .filter((r) => r.scope_type === "project")
    .sort((a, b) =>
      (a.project_name ?? "").localeCompare(b.project_name ?? "", undefined, {
        sensitivity: "base",
      }),
    );
}

/** Project rows whose role carries the admin permission: the server treats
 *  such a row as administrator access everywhere, not on one project. */
export function projectAdminRows(user: UserDetail, roles: Role[]): UserRoleInfo[] {
  const grantsAdmin = new Set(roles.filter((r) => r.permissions.includes(ADMIN)).map((r) => r.id));
  return projectRows(user).filter((r) => grantsAdmin.has(r.role_id));
}

const LIST_GROUPS: AccessKind[] = ["half", "administrator", "member"];

/** People in the order the list shows them: by group, then by name. */
export function listOrder(users: UserDetail[]): UserDetail[] {
  return LIST_GROUPS.flatMap((kind) =>
    users
      .filter((u) => accessKind(u) === kind)
      .sort((x, y) => x.name.localeCompare(y.name, undefined, { sensitivity: "base" })),
  );
}

/** The person to show: the one asked for, else the one already on screen,
 *  else the first in the list. Keeping the one on screen stops the pane
 *  jumping when a change moves that person to another group. */
export function selectedUser(
  users: UserDetail[],
  requested: string | null,
  pinned: string | null,
): UserDetail {
  return (
    users.find((u) => u.id === requested) ??
    users.find((u) => u.id === pinned) ??
    listOrder(users)[0]
  );
}

/** Global rows other than admin: each one reaches every project. */
export function leftoverGlobalRoles(
  user: UserDetail,
  roles: Role[],
): { row: UserRoleInfo; grantsAdmin: boolean }[] {
  const byId = new Map(roles.map((r) => [r.id, r]));
  return user.roles
    .filter((r) => r.scope_type === "global" && r.role_name !== ADMIN)
    .map((row) => ({
      row,
      grantsAdmin: byId.get(row.role_id)?.permissions.includes(ADMIN) ?? false,
    }));
}

/** What copying *source*'s project access onto *target* would add or change.
 *  Nothing is removed; roles that cannot be given per project are skipped. */
export function copyPreview(
  target: UserDetail,
  source: UserDetail,
  roles: Role[],
): { adds: Assignment[]; changes: Assignment[] } {
  const byId = new Map(roles.map((r) => [r.id, r]));
  const current = new Map(projectRows(target).map((r) => [r.scope_id, r.role_id]));
  const adds: Assignment[] = [];
  const changes: Assignment[] = [];
  for (const row of projectRows(source)) {
    const role = byId.get(row.role_id);
    if (!row.scope_id || !role || !isAssignablePerProject(role)) continue;
    const assignment = {
      project_id: row.scope_id,
      project_name: row.project_name ?? row.scope_id,
      role_id: role.id,
      role_name: role.name,
    };
    const had = current.get(row.scope_id);
    if (had === undefined) adds.push(assignment);
    else if (had !== role.id) changes.push(assignment);
  }
  return { adds, changes };
}

// Invite and create-user take a role id. Members get no role row from either,
// so the viewer id is only a placeholder the API requires.
export function memberRoleId(roles: Role[]): string | undefined {
  return roles.find((r) => r.name === "viewer")?.id;
}

export function adminRoleId(roles: Role[]): string | undefined {
  return roles.find((r) => r.name === ADMIN)?.id;
}
