"use client";

import type { ReactNode } from "react";
import { toast } from "sonner";
import { AlertTriangle } from "lucide-react";
import { usersApi, type Role, type UserDetail } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { AccessKindCards, type AccessChoice } from "./AccessKindCards";
import {
  accessKind,
  leftoverGlobalRoles,
  missingAdminHalf,
  projectAdminRows,
  projectRows,
} from "./access";
import { ProjectAccessEditor } from "./ProjectAccessEditor";

function errorDetail(e: unknown, fallback: string): string {
  return (e as { body?: { detail?: string } })?.body?.detail || fallback;
}

function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="mt-3 flex gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
      <div className="space-y-2">{children}</div>
    </div>
  );
}

export function AccessTab({
  user,
  roles,
  users,
  onChanged,
}: {
  user: UserDetail;
  roles: Role[];
  users: UserDetail[];
  onChanged: () => void;
}) {
  const { user: currentUser } = useAuthStore();
  const confirm = useConfirm();
  const isSelf = user.id === currentUser?.id;
  const kind = accessKind(user);
  const missing = missingAdminHalf(user);
  const leftovers = kind === "member" ? leftoverGlobalRoles(user, roles) : [];
  const adminViaProject = kind === "member" ? projectAdminRows(user, roles) : [];

  const setAdministrator = async (on: boolean) => {
    const ok = await confirm({
      title: on ? `Make ${user.name} an administrator?` : `Make ${user.name} a member?`,
      description: on
        ? "They get everything, on every project, including users, integrations, backups and settings."
        : "They keep only the projects listed on their Access tab, with the role given on each.",
      confirmText: on ? "Make administrator" : "Make member",
      tone: "warning",
    });
    if (!ok) return;
    try {
      await usersApi.update(user.id, { is_admin: on });
      toast.success(on ? `${user.name} is now an administrator` : `${user.name} is now a member`);
      onChanged();
    } catch (e) {
      toast.error(errorDetail(e, "Failed to change administrator access"));
    }
  };

  const removeGlobalRole = async (rowId: string, roleName: string) => {
    try {
      await usersApi.removeRole(user.id, rowId);
      toast.success(`Removed the ${roleName} role`);
      onChanged();
    } catch (e) {
      toast.error(errorDetail(e, "Failed to remove the role"));
    }
  };

  const value: AccessChoice | null =
    kind === "administrator" ? "administrator" : kind === "member" ? "member" : null;

  return (
    <div>
      <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        Access level
      </p>
      <AccessKindCards
        value={value}
        onChange={(choice) => setAdministrator(choice === "administrator")}
        disabled={isSelf}
      />
      {isSelf && (
        <p className="mt-2 text-xs text-muted-foreground">
          You can&apos;t remove administrator from your own account.
        </p>
      )}

      {kind === "half" && (
        <Notice>
          <p>
            {missing === "flag"
              ? `${user.name} has the administrator role but not the administrator setting. The server treats them as an administrator, but the admin pages are hidden from them.`
              : `${user.name} has the administrator setting but not the administrator role. The server treats them as an administrator.`}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setAdministrator(true)}>
              Make administrator
            </Button>
            <Button size="sm" variant="outline" onClick={() => setAdministrator(false)} disabled={isSelf}>
              Make member
            </Button>
          </div>
        </Notice>
      )}

      {leftovers.map(({ row, grantsAdmin }) => (
        <Notice key={row.id}>
          <p>
            {grantsAdmin
              ? `${user.name} has full administrator access through the custom role ${row.role_name}.`
              : `${user.name} can see every project. They still have the old global ${row.role_name} role, which overrides the ${projectRows(user).length} projects listed.`}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => removeGlobalRole(row.id, row.role_name ?? "")}
          >
            Remove global {row.role_name}
          </Button>
        </Notice>
      ))}

      {adminViaProject.map((row) => (
        <Notice key={row.id}>
          <p>
            {`${user.name} has full administrator access through the custom role ${row.role_name}, given on ${row.project_name ?? "a project"}: the server applies it everywhere, not only there.`}
          </p>
          <Button
            size="sm"
            variant="outline"
            onClick={() => removeGlobalRole(row.id, row.role_name ?? "")}
          >
            Remove {row.role_name} on {row.project_name}
          </Button>
        </Notice>
      ))}

      {kind === "member" && (
        <div className="mt-6">
          <ProjectAccessEditor user={user} roles={roles} users={users} onChanged={onChanged} />
        </div>
      )}
    </div>
  );
}
