"use client";

import * as React from "react";
import { toast } from "sonner";
import { usersApi, type Role, type UserDetail } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { accessKind, copyPreview, type Assignment } from "./access";

function List({ items }: { items: Assignment[] }) {
  return (
    <ul className="mt-1 max-h-40 space-y-0.5 overflow-y-auto pl-4 text-xs text-muted-foreground">
      {items.map((a) => (
        <li key={a.project_id} className="list-disc">
          {a.project_name} · {a.role_name}
        </li>
      ))}
    </ul>
  );
}

/** Copy another member's project access onto *user*. Adds and changes only. */
export function CopyAccessDialog({
  open,
  onOpenChange,
  user,
  users,
  roles,
  onChanged,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  user: UserDetail;
  users: UserDetail[];
  roles: Role[];
  onChanged: () => void;
}) {
  const [sourceId, setSourceId] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const others = users.filter((u) => u.id !== user.id && accessKind(u) === "member");
  const source = others.find((u) => u.id === sourceId);
  const preview = source ? copyPreview(user, source, roles) : null;
  const total = preview ? preview.adds.length + preview.changes.length : 0;

  const apply = async () => {
    if (!preview || total === 0) return;
    setBusy(true);
    try {
      await usersApi.assignProjectRoles(
        user.id,
        [...preview.adds, ...preview.changes].map(({ project_id, role_id }) => ({ project_id, role_id })),
      );
      toast.success(`Copied access from ${source?.name}`);
      onOpenChange(false);
      setSourceId("");
      onChanged();
    } catch (e) {
      toast.error((e as { body?: { detail?: string } })?.body?.detail || "Failed to copy access");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Copy access from…</DialogTitle>
          <DialogDescription>
            Give {user.name} the same projects and roles as another member. Nothing {user.name}{" "}
            already has on other projects is removed.
          </DialogDescription>
        </DialogHeader>
        <Select
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
          placeholder="Choose a member"
          options={others.map((u) => ({ value: u.id, label: `${u.name} (${u.email})` }))}
        />
        {preview && total === 0 && (
          <p className="text-sm text-muted-foreground">
            Nothing to copy: {user.name} already has all of it.
          </p>
        )}
        {preview && total > 0 && (
          <div className="space-y-2 text-sm">
            <p>
              Adds {preview.adds.length} project{preview.adds.length === 1 ? "" : "s"}, changes{" "}
              {preview.changes.length} role{preview.changes.length === 1 ? "" : "s"}.
            </p>
            {preview.adds.length > 0 && (
              <details>
                <summary className="cursor-pointer text-xs">Projects added</summary>
                <List items={preview.adds} />
              </details>
            )}
            {preview.changes.length > 0 && (
              <details>
                <summary className="cursor-pointer text-xs">Roles changed</summary>
                <List items={preview.changes} />
              </details>
            )}
          </div>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={apply} disabled={busy || total === 0}>
            {busy ? "Copying…" : "Copy access"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
