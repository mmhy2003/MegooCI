"use client";

import * as React from "react";
import { toast } from "sonner";
import { CheckCircle2, ShieldOff, Trash2, XCircle } from "lucide-react";
import { usersApi, type Role, type UserDetail } from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { cn } from "@/lib/utils";
import { Avatar } from "./UserList";
import { AccessTab } from "./AccessTab";

type DetailTab = "access" | "security" | "account";

function errorDetail(e: unknown, fallback: string): string {
  return (e as { body?: { detail?: string } })?.body?.detail || fallback;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1 border-b py-3 last:border-0 sm:grid-cols-[160px_1fr] sm:items-center">
      <span className="text-sm text-muted-foreground">{label}</span>
      <div className="flex flex-wrap items-center gap-2 text-sm">{children}</div>
    </div>
  );
}

export function UserDetailPane({
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
  const [tab, setTab] = React.useState<DetailTab>("access");
  const isSelf = user.id === currentUser?.id;

  const toggleActive = async () => {
    const action = user.is_active ? "Deactivate" : "Activate";
    const ok = await confirm({
      title: `${action} user?`,
      description: `Are you sure you want to ${action.toLowerCase()} ${user.name} (${user.email})?`,
      confirmText: action,
      tone: user.is_active ? "warning" : undefined,
    });
    if (!ok) return;
    try {
      await usersApi.update(user.id, { is_active: !user.is_active });
      toast.success(`User ${action.toLowerCase()}d`);
      onChanged();
    } catch (e) {
      toast.error(errorDetail(e, `Failed to ${action.toLowerCase()} user`));
    }
  };

  const resetTwoStep = async () => {
    const ok = await confirm({
      title: "Reset two-step verification?",
      description: `This removes the authenticator app, passkeys and recovery codes of ${user.name} (${user.email}). They sign in with their password alone until they set it up again; where it is required, they are asked to at their next sign-in.`,
      confirmText: "Reset",
      tone: "warning",
    });
    if (!ok) return;
    try {
      await usersApi.resetTwoStep(user.id);
      toast.success(`Two-step verification reset for ${user.name}`);
      onChanged();
    } catch (e) {
      toast.error(errorDetail(e, "Failed to reset two-step verification"));
    }
  };

  const deleteUser = async () => {
    const ok = await confirm({
      title: "Delete user permanently?",
      description: `This will permanently delete ${user.name} (${user.email}) and remove all their role assignments. This action cannot be undone.`,
      confirmText: "Delete",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await usersApi.delete(user.id);
      toast.success(`User ${user.name} deleted`);
      onChanged();
    } catch (e) {
      toast.error(errorDetail(e, "Failed to delete user"));
    }
  };

  const resetReason = isSelf
    ? "Change your own two-step verification in Settings"
    : !user.two_step_enabled
      ? "Only when two-step is on"
      : null;

  return (
    <div className="p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div className="flex min-w-0 items-center gap-3">
          <Avatar user={user} size="lg" />
          <div className="min-w-0">
            <p className="truncate text-lg font-semibold">{user.name}</p>
            <p className="truncate text-xs text-muted-foreground">{user.email}</p>
          </div>
        </div>
        <div className="flex gap-1.5">
          {!user.is_active && <Badge variant="failed">Inactive</Badge>}
          <Badge variant={user.two_step_enabled ? "success" : "warning"}>
            {user.two_step_enabled ? "Two-step on" : "Two-step off"}
          </Badge>
        </div>
      </div>

      <div role="tablist" className="mb-4 mt-1 flex gap-1 border-b">
        {(["access", "security", "account"] as DetailTab[]).map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm font-medium capitalize transition-colors",
              tab === t
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
            {t === "security" && !user.two_step_enabled && (
              <span className="ml-1.5 inline-block h-1.5 w-1.5 rounded-full bg-amber-500 align-middle" />
            )}
          </button>
        ))}
      </div>

      {tab === "access" && (
        <AccessTab user={user} roles={roles} users={users} onChanged={onChanged} />
      )}

      {tab === "security" && (
        <div>
          <Row label="Two-step verification">
            <Badge variant={user.two_step_enabled ? "success" : "warning"}>
              {user.two_step_enabled ? "On" : "Off"}
            </Badge>
          </Row>
          <Row label="Reset two-step">
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={resetTwoStep}
              disabled={resetReason !== null}
            >
              <ShieldOff className="h-3.5 w-3.5" /> Reset
            </Button>
            {resetReason && <span className="text-xs text-muted-foreground">{resetReason}</span>}
          </Row>
        </div>
      )}

      {tab === "account" && (
        <div>
          <Row label="Member since">
            {new Date(user.created_at).toLocaleDateString(undefined, {
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </Row>
          <Row label="Status">
            <Badge variant={user.is_active ? "success" : "failed"}>
              {user.is_active ? "Active" : "Inactive"}
            </Badge>
            <Button variant="outline" size="sm" className="gap-1.5" onClick={toggleActive} disabled={isSelf}>
              {user.is_active ? (
                <>
                  <XCircle className="h-3.5 w-3.5" /> Deactivate
                </>
              ) : (
                <>
                  <CheckCircle2 className="h-3.5 w-3.5" /> Activate
                </>
              )}
            </Button>
          </Row>
          <Row label="Delete">
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5 text-destructive hover:text-destructive"
              onClick={deleteUser}
              disabled={isSelf}
            >
              <Trash2 className="h-3.5 w-3.5" /> Delete user…
            </Button>
            {isSelf && <span className="text-xs text-muted-foreground">Not your own account</span>}
          </Row>
        </div>
      )}
    </div>
  );
}
