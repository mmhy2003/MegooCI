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
