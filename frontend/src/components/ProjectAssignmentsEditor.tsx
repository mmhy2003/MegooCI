"use client";

import { useEffect, useState } from "react";
import { toast } from "sonner";
import { X } from "lucide-react";
import {
  usersApi,
  projectsApi,
  rolesApi,
  type UserDetail,
  type Role,
  type Project,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";

export function ProjectAssignmentsEditor({
  user,
  onChanged,
}: {
  user: UserDetail;
  onChanged: () => void;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [projectId, setProjectId] = useState("");
  const [roleId, setRoleId] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    projectsApi
      .listAll()
      .then(setProjects)
      .catch(() => {});
    rolesApi
      .list()
      .then((rs) => setRoles(rs.filter((r) => r.name !== "admin")))
      .catch(() => {});
  }, []);

  const projectRoles = user.roles.filter((r) => r.scope_type === "project");

  async function handleAdd() {
    if (!projectId || !roleId) return;
    setBusy(true);
    try {
      await usersApi.assignRole(user.id, {
        role_id: roleId,
        scope_type: "project",
        scope_id: projectId,
      });
      setProjectId("");
      setRoleId("");
      onChanged();
    } catch (e: unknown) {
      const msg =
        (e as { body?: { detail?: string } })?.body?.detail ||
        (e instanceof Error ? e.message : "Failed to assign project");
      toast.error(msg);
    } finally {
      setBusy(false);
    }
  }

  async function handleRemove(userRoleId: string) {
    setBusy(true);
    try {
      await usersApi.removeRole(user.id, userRoleId);
      onChanged();
    } catch {
      toast.error("Failed to remove project assignment");
    } finally {
      setBusy(false);
    }
  }

  if (user.is_admin) {
    return (
      <p className="text-sm text-muted-foreground">
        Admin — access to all projects.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {projectRoles.length === 0 && (
          <span className="text-xs text-muted-foreground italic">
            No projects assigned.
          </span>
        )}
        {projectRoles.map((r) => (
          <Badge
            key={r.id}
            variant="secondary"
            className="gap-1 pr-1 text-xs"
          >
            {r.project_name ?? r.scope_id ?? "—"} · {r.role_name}
            <button
              type="button"
              disabled={busy}
              onClick={() => handleRemove(r.id)}
              aria-label={`Remove ${r.project_name ?? r.scope_id} assignment`}
              className="ml-0.5 rounded hover:text-destructive disabled:opacity-50"
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
      </div>
      <div className="flex flex-wrap gap-2">
        <div className="w-44">
          <Select
            value={projectId}
            onChange={(e) => setProjectId(e.target.value)}
            placeholder="Select project…"
            options={projects.map((p) => ({ value: p.id, label: p.name }))}
          />
        </div>
        <div className="w-32">
          <Select
            value={roleId}
            onChange={(e) => setRoleId(e.target.value)}
            placeholder="Role…"
            options={roles.map((r) => ({ value: r.id, label: r.name }))}
          />
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={busy || !projectId || !roleId}
          onClick={handleAdd}
        >
          Assign
        </Button>
      </div>
    </div>
  );
}
