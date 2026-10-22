"use client";

import { Check, Minus } from "lucide-react";
import type { Role } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";

const AREAS = ["Projects & pipelines", "Builds & artifacts", "Secrets", "Server"] as const;

/** Every permission in backend/app/core/permissions.py, as a sentence. */
const PERMISSION_SENTENCES: Record<string, { area: string; sentence: string }> = {
  "projects.read": { area: "Projects & pipelines", sentence: "See projects" },
  "projects.manage": { area: "Projects & pipelines", sentence: "Create and change projects" },
  "pipelines.read": { area: "Projects & pipelines", sentence: "See pipelines" },
  "pipelines.manage": { area: "Projects & pipelines", sentence: "Create and change pipelines" },
  "builds.read": { area: "Builds & artifacts", sentence: "See builds and their logs" },
  "builds.manage": { area: "Builds & artifacts", sentence: "Start, cancel and retry builds" },
  "artifacts.read": { area: "Builds & artifacts", sentence: "See and download artifacts" },
  "artifacts.manage": { area: "Builds & artifacts", sentence: "Delete artifacts" },
  "secrets.read": { area: "Secrets", sentence: "See secrets" },
  "secrets.manage": { area: "Secrets", sentence: "Create and change secrets" },
  admin: { area: "Server", sentence: "Everything, on every project" },
  "users.read": { area: "Server", sentence: "See users" },
  "users.manage": { area: "Server", sentence: "Manage users" },
  "roles.manage": { area: "Server", sentence: "Manage roles" },
  "invites.manage": { area: "Server", sentence: "Invite people" },
  "settings.manage": { area: "Server", sentence: "Change server settings" },
  "agents.read": { area: "Server", sentence: "See build agents" },
  "agents.manage": { area: "Server", sentence: "Manage build agents" },
  "git_connections.manage": { area: "Server", sentence: "Manage git connections" },
  "registry.read": { area: "Server", sentence: "Pull from the container registry" },
  "registry.push": { area: "Server", sentence: "Push to the container registry" },
  "registry.manage": { area: "Server", sentence: "Manage the container registry" },
};

function orderedRoles(roles: Role[]): Role[] {
  const rank = (r: Role) => (r.name === "admin" ? 0 : r.is_system ? 1 : 2);
  return [...roles].sort((a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name));
}

export function RolesTab({ roles, loading }: { roles: Role[]; loading: boolean }) {
  if (loading) return <Skeleton className="h-64 w-full" />;

  const columns = orderedRoles(roles);
  const permissions = new Set([
    ...Object.keys(PERMISSION_SENTENCES),
    ...roles.flatMap((r) => r.permissions),
  ]);
  const groups = [...AREAS, "Other"].map((area) => ({
    area,
    rows: [...permissions]
      .filter((p) => (PERMISSION_SENTENCES[p]?.area ?? "Other") === area)
      .map((p) => ({ name: p, sentence: PERMISSION_SENTENCES[p]?.sentence ?? p })),
  }));

  return (
    <div className="space-y-2">
      <p className="text-sm text-muted-foreground">
        What each role allows. Developer, viewer and custom roles are given per project; an
        Administrator has everything.
      </p>
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b bg-muted/40 text-xs uppercase tracking-wide text-muted-foreground">
              <th className="px-3 py-2 text-left font-medium">Permission</th>
              {columns.map((role) => (
                <th key={role.id} className="px-3 py-2 text-center font-medium" title={role.description ?? undefined}>
                  {role.name === "admin" ? "Administrator" : role.name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {groups
              .filter((g) => g.rows.length > 0)
              .map((group) => [
                <tr key={group.area}>
                  <td
                    colSpan={columns.length + 1}
                    className="px-3 pb-1 pt-3 text-xs uppercase tracking-wide text-muted-foreground"
                  >
                    {group.area}
                  </td>
                </tr>,
                ...group.rows.map((row) => (
                  <tr key={row.name} className="border-b last:border-0">
                    <td className="px-3 py-2" title={row.name}>
                      {row.sentence}
                    </td>
                    {columns.map((role) => (
                      <td key={role.id} className="px-3 py-2 text-center">
                        {role.permissions.includes(row.name) || role.name === "admin" ? (
                          <Check className="mx-auto h-4 w-4 text-emerald-600 dark:text-emerald-400" aria-label="yes" />
                        ) : (
                          <Minus className="mx-auto h-4 w-4 text-muted-foreground/40" aria-label="no" />
                        )}
                      </td>
                    ))}
                  </tr>
                )),
              ])}
          </tbody>
        </table>
      </div>
    </div>
  );
}
