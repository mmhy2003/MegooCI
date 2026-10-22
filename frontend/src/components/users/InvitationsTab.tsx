"use client";

import { toast } from "sonner";
import { CheckCircle2, Clock, RefreshCw, Trash2, XCircle } from "lucide-react";
import { invitesApi, type Invite } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfirm } from "@/components/ui/confirm-dialog";

function InviteStatusBadge({ status }: { status: string }) {
  if (status === "pending")
    return (
      <Badge variant="pending" className="gap-1">
        <Clock className="h-3 w-3" /> Pending
      </Badge>
    );
  if (status === "accepted")
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" /> Accepted
      </Badge>
    );
  if (status === "expired")
    return (
      <Badge variant="cancelled" className="gap-1">
        <Clock className="h-3 w-3" /> Expired
      </Badge>
    );
  if (status === "revoked")
    return (
      <Badge variant="failed" className="gap-1">
        <XCircle className="h-3 w-3" /> Revoked
      </Badge>
    );
  return <Badge variant="secondary">{status}</Badge>;
}

export function InvitationsTab({
  invites,
  loading,
  onChanged,
}: {
  invites: Invite[];
  loading: boolean;
  onChanged: () => void;
}) {
  const confirm = useConfirm();

  const handleRevoke = async (invite: Invite) => {
    const ok = await confirm({
      title: "Revoke invitation?",
      description: `This will invalidate the invitation sent to ${invite.email}.`,
      confirmText: "Revoke",
      tone: "warning",
    });
    if (!ok) return;
    try {
      await invitesApi.revoke(invite.id);
      toast.success("Invitation revoked");
      onChanged();
    } catch {
      toast.error("Failed to revoke invitation");
    }
  };

  const handleResend = async (invite: Invite) => {
    try {
      await invitesApi.resend(invite.id);
      toast.success(`Invitation resent to ${invite.email}`);
      onChanged();
    } catch {
      toast.error("Failed to resend invitation");
    }
  };

  const handleCleanup = async () => {
    const nonPending = invites.filter((i) => i.status !== "pending").length;
    const ok = await confirm({
      title: "Clear invitation history?",
      description: `This will permanently delete ${nonPending} non-pending invitation${nonPending === 1 ? "" : "s"} (accepted, revoked, expired). Pending invitations will not be affected.`,
      confirmText: "Clear",
      tone: "warning",
    });
    if (!ok) return;
    try {
      const result = await invitesApi.cleanup();
      toast.success(`Cleared ${result.deleted} old invitation${result.deleted === 1 ? "" : "s"}`);
      onChanged();
    } catch {
      toast.error("Failed to clear invitation history");
    }
  };

  if (loading) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          Pending and past invitations. Invited people join as an Administrator or a Member.
        </p>
        {invites.some((i) => i.status !== "pending") && (
          <Button
            variant="ghost"
            size="sm"
            onClick={handleCleanup}
            className="shrink-0 gap-1.5 text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear history
          </Button>
        )}
      </div>
      {invites.length === 0 ? (
        <p className="rounded-lg border py-10 text-center text-sm text-muted-foreground">
          No invitations yet. Click &quot;Invite member&quot; to get started.
        </p>
      ) : (
        <div className="space-y-2">
          {invites.map((inv) => (
            <div
              key={inv.id}
              className="flex flex-col gap-2 rounded-lg border px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{inv.email}</span>
                  <InviteStatusBadge status={inv.status} />
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">
                    {inv.role_name === "admin" ? "Administrator" : "Member"}
                  </span>
                  {inv.creator_name && <span>Invited by {inv.creator_name}</span>}
                  <span>Expires {new Date(inv.expires_at).toLocaleDateString()}</span>
                </div>
              </div>
              {inv.status === "pending" && (
                <div className="flex shrink-0 gap-1">
                  <Button variant="ghost" size="sm" onClick={() => handleResend(inv)} title="Resend invitation">
                    <RefreshCw className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleRevoke(inv)}
                    className="text-destructive hover:text-destructive"
                    title="Revoke invitation"
                  >
                    <XCircle className="h-3.5 w-3.5" />
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
