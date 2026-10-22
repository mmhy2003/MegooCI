"use client";

import * as React from "react";
import { toast } from "sonner";
import { Copy, X } from "lucide-react";
import { projectsApi, usersApi, type Project, type Role, type UserDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { isAssignablePerProject, projectRows } from "./access";
import { CopyAccessDialog } from "./CopyAccessDialog";
import { ProjectMultiSelect } from "./ProjectMultiSelect";

function errorDetail(e: unknown, fallback: string): string {
  return (e as { body?: { detail?: string } })?.body?.detail || fallback;
}

function roleTone(roleName: string | null): string {
  if (roleName === "developer") return "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400 border-transparent";
  if (roleName === "viewer") return "bg-secondary text-secondary-foreground border-transparent";
  return "";
}

/** A member's project access: change a role in place, remove, add several, copy. */
export function ProjectAccessEditor({
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
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [filter, setFilter] = React.useState("");
  const [adding, setAdding] = React.useState<string[]>([]);
  const assignable = roles.filter(isAssignablePerProject);
  const [addRoleId, setAddRoleId] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [copyOpen, setCopyOpen] = React.useState(false);

  React.useEffect(() => {
    projectsApi.listAll().then(setProjects).catch(() => toast.error("Failed to load projects"));
  }, []);

  const roleForAdd = addRoleId || assignable.find((r) => r.name === "viewer")?.id || assignable[0]?.id || "";
  const rows = projectRows(user);
  const q = filter.trim().toLowerCase();
  const shown = rows.filter((r) => !q || (r.project_name ?? "").toLowerCase().includes(q));
  const roleOptions = assignable.map((r) => ({ value: r.id, label: r.name }));

  const changeRole = async (projectId: string, roleId: string) => {
    try {
      await usersApi.assignRole(user.id, { role_id: roleId, scope_type: "project", scope_id: projectId });
      toast.success("Role changed");
      onChanged();
    } catch (e) {
      toast.error(errorDetail(e, "Failed to change the role"));
    }
  };

  const remove = async (rowId: string, projectName: string) => {
    try {
      await usersApi.removeRole(user.id, rowId);
      toast.success(`Removed from ${projectName}`);
      onChanged();
    } catch (e) {
      toast.error(errorDetail(e, "Failed to remove access"));
    }
  };

  const add = async () => {
    if (adding.length === 0 || !roleForAdd) return;
    setBusy(true);
    try {
      await usersApi.assignProjectRoles(
        user.id,
        adding.map((project_id) => ({ project_id, role_id: roleForAdd })),
      );
      toast.success(`Added ${adding.length} project${adding.length === 1 ? "" : "s"}`);
      setAdding([]);
      onChanged();
    } catch (e) {
      toast.error(errorDetail(e, "Failed to add projects"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="font-medium">
          Projects <span className="text-muted-foreground">· {rows.length}</span>
        </p>
        <div className="flex items-center gap-2">
          <Input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter projects…"
            className="h-8 w-44"
          />
          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setCopyOpen(true)}>
            <Copy className="h-3.5 w-3.5" /> Copy access from…
          </Button>
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="mt-3 rounded-lg border border-dashed py-6 text-center text-sm text-muted-foreground">
          No projects yet. Add some below.
        </p>
      ) : (
        <div className="mt-2 grid gap-x-7 lg:grid-cols-2">
          {shown.map((row) => {
            const current = roles.find((r) => r.id === row.role_id);
            // A role that can no longer be given per project still shows, so
            // the page never hides access someone has.
            const options =
              current && !isAssignablePerProject(current)
                ? [...roleOptions, { value: current.id, label: current.name }]
                : roleOptions;
            return (
              <div
                key={row.id}
                className="grid grid-cols-[1fr_120px_28px] items-center gap-2 border-b py-1.5 text-sm"
              >
                <span className="truncate" title={row.project_name ?? undefined}>
                  {row.project_name ?? row.scope_id}
                </span>
                <Select
                  value={row.role_id}
                  onChange={(e) => row.scope_id && changeRole(row.scope_id, e.target.value)}
                  options={options}
                  className={cn("h-7 text-xs", roleTone(row.role_name))}
                />
                <button
                  type="button"
                  onClick={() => remove(row.id, row.project_name ?? "the project")}
                  className="grid h-7 w-7 place-items-center rounded text-muted-foreground hover:bg-muted hover:text-foreground"
                  title={`Remove from ${row.project_name}`}
                  aria-label={`Remove from ${row.project_name}`}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="mt-3 flex flex-col gap-2 rounded-lg border border-dashed bg-muted/30 p-2 sm:flex-row sm:items-center">
        <ProjectMultiSelect
          projects={projects}
          exclude={new Set(rows.map((r) => r.scope_id ?? ""))}
          value={adding}
          onChange={setAdding}
        />
        <div className="flex gap-2">
          <div className="w-36">
            <Select
              value={roleForAdd}
              onChange={(e) => setAddRoleId(e.target.value)}
              options={roleOptions.map((o) => ({ ...o, label: `as ${o.label}` }))}
            />
          </div>
          <Button onClick={add} disabled={busy || adding.length === 0 || !roleForAdd}>
            {busy ? "Adding…" : "Add"}
          </Button>
        </div>
      </div>

      <CopyAccessDialog
        open={copyOpen}
        onOpenChange={setCopyOpen}
        user={user}
        users={users}
        roles={roles}
        onChanged={onChanged}
      />
    </div>
  );
}
