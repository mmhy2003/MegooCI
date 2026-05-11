"use client";

import * as React from "react";
import { toast } from "sonner";
import {
  Users,
  Mail,
  Shield,
  ShieldCheck,
  Eye,
  Code2,
  UserPlus,
  Plus,
  KeyRound,
  MoreHorizontal,
  Copy,
  RefreshCw,
  XCircle,
  CheckCircle2,
  Clock,
  Send,
  Trash2,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { RequireAdmin } from "@/components/require-permission";
import { useAuthStore } from "@/lib/auth";
import {
  usersApi,
  rolesApi,
  invitesApi,
  type UserDetail,
  type UserCreated,
  type Role,
  type Invite,
  type InviteCreated,
} from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { useConfirm } from "@/components/ui/confirm-dialog";

function roleIcon(name: string) {
  if (name === "admin") return <ShieldCheck className="h-3.5 w-3.5" />;
  if (name === "developer") return <Code2 className="h-3.5 w-3.5" />;
  return <Eye className="h-3.5 w-3.5" />;
}

function roleBadgeVariant(name: string): "default" | "secondary" | "success" {
  if (name === "admin") return "default";
  if (name === "developer") return "success";
  return "secondary";
}

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

export default function AdminUsersPage() {
  const { user: currentUser } = useAuthStore();
  const confirm = useConfirm();

  const [users, setUsers] = React.useState<UserDetail[]>([]);
  const [roles, setRoles] = React.useState<Role[]>([]);
  const [invites, setInvites] = React.useState<Invite[]>([]);
  const [loading, setLoading] = React.useState(true);

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [inviteEmail, setInviteEmail] = React.useState("");
  const [inviteRoleId, setInviteRoleId] = React.useState("");
  const [inviteSending, setInviteSending] = React.useState(false);
  const [lastInviteLink, setLastInviteLink] = React.useState<string | null>(null);

  const [roleDialogOpen, setRoleDialogOpen] = React.useState(false);
  const [roleDialogUserId, setRoleDialogUserId] = React.useState<string | null>(null);
  const [roleDialogRoleId, setRoleDialogRoleId] = React.useState("");
  const [roleAssigning, setRoleAssigning] = React.useState(false);

  const [createOpen, setCreateOpen] = React.useState(false);
  const [createEmail, setCreateEmail] = React.useState("");
  const [createName, setCreateName] = React.useState("");
  const [createRoleId, setCreateRoleId] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const [createdPassword, setCreatedPassword] = React.useState<string | null>(null);
  const [createdUserEmail, setCreatedUserEmail] = React.useState("");

  const isAdmin = currentUser?.is_admin ?? false;

  const loadData = React.useCallback(async () => {
    if (!isAdmin) {
      setLoading(false);
      return;
    }
    try {
      const [u, r, i] = await Promise.all([
        usersApi.list(),
        rolesApi.list(),
        invitesApi.list(),
      ]);
      setUsers(u);
      setRoles(r);
      setInvites(i);
    } catch {
      toast.error("Failed to load user management data");
    } finally {
      setLoading(false);
    }
  }, [isAdmin]);

  React.useEffect(() => {
    loadData();
  }, [loadData]);

  const handleInvite = async () => {
    if (!inviteEmail || !inviteRoleId) return;
    setInviteSending(true);
    try {
      const result: InviteCreated = await invitesApi.create({
        email: inviteEmail,
        role_id: inviteRoleId,
      });
      toast.success(`Invitation sent to ${inviteEmail}`);
      setLastInviteLink(result.invite_link);
      setInviteEmail("");
      loadData();
    } catch (e: unknown) {
      const msg = (e as { body?: { detail?: string } })?.body?.detail || "Failed to send invitation";
      toast.error(msg);
    } finally {
      setInviteSending(false);
    }
  };

  const handleCopyLink = () => {
    if (lastInviteLink) {
      navigator.clipboard.writeText(lastInviteLink);
      toast.success("Invite link copied to clipboard");
    }
  };

  const handleRevokeInvite = async (invite: Invite) => {
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
      loadData();
    } catch {
      toast.error("Failed to revoke invitation");
    }
  };

  const handleResendInvite = async (invite: Invite) => {
    try {
      await invitesApi.resend(invite.id);
      toast.success(`Invitation resent to ${invite.email}`);
      loadData();
    } catch {
      toast.error("Failed to resend invitation");
    }
  };

  const handleToggleActive = async (u: UserDetail) => {
    const action = u.is_active ? "deactivate" : "activate";
    const ok = await confirm({
      title: `${action.charAt(0).toUpperCase() + action.slice(1)} user?`,
      description: `Are you sure you want to ${action} ${u.name} (${u.email})?`,
      confirmText: action.charAt(0).toUpperCase() + action.slice(1),
      tone: u.is_active ? "warning" : undefined,
    });
    if (!ok) return;
    try {
      await usersApi.update(u.id, { is_active: !u.is_active });
      toast.success(`User ${action}d`);
      loadData();
    } catch (e: unknown) {
      const msg = (e as { body?: { detail?: string } })?.body?.detail || `Failed to ${action} user`;
      toast.error(msg);
    }
  };

  const handleDeleteUser = async (u: UserDetail) => {
    const ok = await confirm({
      title: "Delete user permanently?",
      description: `This will permanently delete ${u.name} (${u.email}) and remove all their role assignments. This action cannot be undone.`,
      confirmText: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    try {
      await usersApi.delete(u.id);
      toast.success(`User ${u.name} deleted`);
      loadData();
    } catch (e: unknown) {
      const msg = (e as { body?: { detail?: string } })?.body?.detail || "Failed to delete user";
      toast.error(msg);
    }
  };

  const handleToggleAdmin = async (u: UserDetail) => {
    const action = u.is_admin ? "Remove admin" : "Make admin";
    const ok = await confirm({
      title: `${action}?`,
      description: `Are you sure you want to ${action.toLowerCase()} for ${u.name}?`,
      confirmText: action,
      tone: "warning",
    });
    if (!ok) return;
    try {
      await usersApi.update(u.id, { is_admin: !u.is_admin });
      toast.success(`Admin status updated`);
      loadData();
    } catch (e: unknown) {
      const msg = (e as { body?: { detail?: string } })?.body?.detail || "Failed to update admin status";
      toast.error(msg);
    }
  };

  const openAssignRole = (userId: string) => {
    setRoleDialogUserId(userId);
    setRoleDialogRoleId(roles[0]?.id || "");
    setRoleDialogOpen(true);
  };

  const handleAssignRole = async () => {
    if (!roleDialogUserId || !roleDialogRoleId) return;
    setRoleAssigning(true);
    try {
      await usersApi.assignRole(roleDialogUserId, { role_id: roleDialogRoleId });
      toast.success("Role assigned");
      setRoleDialogOpen(false);
      loadData();
    } catch (e: unknown) {
      const msg = (e as { body?: { detail?: string } })?.body?.detail || "Failed to assign role";
      toast.error(msg);
    } finally {
      setRoleAssigning(false);
    }
  };

  const handleRemoveRole = async (userId: string, userRoleId: string, roleName: string) => {
    const ok = await confirm({
      title: "Remove role?",
      description: `Remove the "${roleName}" role from this user?`,
      confirmText: "Remove",
      tone: "warning",
    });
    if (!ok) return;
    try {
      await usersApi.removeRole(userId, userRoleId);
      toast.success("Role removed");
      loadData();
    } catch {
      toast.error("Failed to remove role");
    }
  };

  const handleCreateUser = async () => {
    if (!createEmail || !createName || !createRoleId) return;
    setCreating(true);
    try {
      const result: UserCreated = await usersApi.create({
        email: createEmail,
        name: createName,
        role_id: createRoleId,
      });
      toast.success(`User ${createEmail} created`);
      setCreatedPassword(result.generated_password);
      setCreatedUserEmail(createEmail);
      setCreateEmail("");
      setCreateName("");
      loadData();
    } catch (e: unknown) {
      const msg = (e as { body?: { detail?: string } })?.body?.detail || "Failed to create user";
      toast.error(msg);
    } finally {
      setCreating(false);
    }
  };

  const handleCopyPassword = () => {
    if (createdPassword) {
      navigator.clipboard.writeText(createdPassword);
      toast.success("Password copied to clipboard");
    }
  };

  if (!isAdmin) {
    return (
      <RequireAdmin>
        <AppLayout>
          <div />
        </AppLayout>
      </RequireAdmin>
    );
  }

  return (
    <AppLayout>
      <div className="mx-auto max-w-5xl space-y-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
              User Management
            </h1>
            <p className="text-sm text-muted-foreground">
              Manage users, roles, and invitations.
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => { setCreateOpen(true); setCreatedPassword(null); }}
              className="gap-2"
            >
              <Plus className="h-4 w-4" />
              Create User
            </Button>
            <Button onClick={() => { setInviteOpen(true); setLastInviteLink(null); }} className="gap-2">
              <UserPlus className="h-4 w-4" />
              Invite Member
            </Button>
          </div>
        </div>

        {/* Users Table */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Users className="h-5 w-5" />
              Users
              {!loading && (
                <Badge variant="secondary" className="ml-2">{users.length}</Badge>
              )}
            </CardTitle>
            <CardDescription>All registered users and their roles.</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-16 w-full" />
                ))}
              </div>
            ) : users.length === 0 ? (
              <p className="py-8 text-center text-muted-foreground">No users found.</p>
            ) : (
              <div className="space-y-2">
                {users.map((u) => (
                  <div
                    key={u.id}
                    className="flex flex-col gap-3 rounded-lg border px-4 py-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">{u.name}</span>
                        {u.is_admin && (
                          <Badge variant="default" className="gap-1 text-[10px]">
                            <ShieldCheck className="h-3 w-3" /> Admin
                          </Badge>
                        )}
                        {!u.is_active && (
                          <Badge variant="failed" className="text-[10px]">Inactive</Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground truncate">{u.email}</p>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {u.roles.map((r) => (
                          <Badge
                            key={r.id}
                            variant={roleBadgeVariant(r.role_name || "")}
                            className="gap-1 cursor-pointer hover:opacity-80"
                            title={`Click to remove "${r.role_name}" role`}
                            onClick={() =>
                              handleRemoveRole(u.id, r.id, r.role_name || "Unknown")
                            }
                          >
                            {roleIcon(r.role_name || "")}
                            {r.role_name}
                            {r.scope_type !== "global" && (
                              <span className="opacity-60">({r.scope_type})</span>
                            )}
                          </Badge>
                        ))}
                        {u.roles.length === 0 && (
                          <span className="text-xs text-muted-foreground italic">No role assigned</span>
                        )}
                      </div>
                    </div>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" size="icon" className="shrink-0">
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onClick={() => openAssignRole(u.id)}>
                          <Shield className="mr-2 h-4 w-4" /> Assign role
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => handleToggleAdmin(u)}
                          disabled={u.id === currentUser?.id}
                        >
                          <ShieldCheck className="mr-2 h-4 w-4" />
                          {u.is_admin ? "Remove admin" : "Make admin"}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          onClick={() => handleToggleActive(u)}
                          disabled={u.id === currentUser?.id}
                          className={u.is_active ? "text-destructive" : ""}
                        >
                          {u.is_active ? (
                            <>
                              <XCircle className="mr-2 h-4 w-4" /> Deactivate
                            </>
                          ) : (
                            <>
                              <CheckCircle2 className="mr-2 h-4 w-4" /> Activate
                            </>
                          )}
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => handleDeleteUser(u)}
                          disabled={u.id === currentUser?.id}
                          className="text-destructive"
                        >
                          <Trash2 className="mr-2 h-4 w-4" /> Delete user
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Invitations */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mail className="h-5 w-5" />
              Invitations
              {!loading && (
                <Badge variant="secondary" className="ml-2">
                  {invites.filter((i) => i.status === "pending").length} pending
                </Badge>
              )}
            </CardTitle>
            <CardDescription>
              Pending and past invitations. Invited users join with the
              assigned role.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))}
              </div>
            ) : invites.length === 0 ? (
              <p className="py-8 text-center text-muted-foreground">
                No invitations yet. Click &quot;Invite Member&quot; to get started.
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
                        <span className="text-sm font-medium truncate">{inv.email}</span>
                        <InviteStatusBadge status={inv.status} />
                      </div>
                      <div className="mt-1 flex items-center gap-3 text-xs text-muted-foreground">
                        <span>
                          Role: <span className="font-medium text-foreground">{inv.role_name}</span>
                        </span>
                        {inv.creator_name && (
                          <span>Invited by {inv.creator_name}</span>
                        )}
                        <span>
                          Expires {new Date(inv.expires_at).toLocaleDateString()}
                        </span>
                      </div>
                    </div>
                    {inv.status === "pending" && (
                      <div className="flex gap-1 shrink-0">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleResendInvite(inv)}
                          title="Resend invitation"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRevokeInvite(inv)}
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
          </CardContent>
        </Card>

        {/* Roles Reference */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              Roles
            </CardTitle>
            <CardDescription>Available roles and their permissions.</CardDescription>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))}
              </div>
            ) : (
              <div className="space-y-2">
                {roles.map((r) => (
                  <div
                    key={r.id}
                    className="rounded-lg border px-4 py-3"
                  >
                    <div className="flex items-center gap-2">
                      <Badge variant={roleBadgeVariant(r.name)} className="gap-1">
                        {roleIcon(r.name)} {r.name}
                      </Badge>
                      {r.is_system && (
                        <span className="text-[10px] text-muted-foreground">(system)</span>
                      )}
                    </div>
                    {r.description && (
                      <p className="mt-1 text-xs text-muted-foreground">{r.description}</p>
                    )}
                    <div className="mt-2 flex flex-wrap gap-1">
                      {r.permissions.map((p) => (
                        <code
                          key={p}
                          className="rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground"
                        >
                          {p}
                        </code>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Invite Dialog */}
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invite a new member</DialogTitle>
            <DialogDescription>
              Send an invitation email with a one-time sign-up link.
              {!lastInviteLink && " The user will be assigned the selected role on sign-up."}
            </DialogDescription>
          </DialogHeader>

          {lastInviteLink ? (
            <div className="space-y-4">
              <div className="rounded-lg border bg-muted/50 p-4">
                <p className="text-sm font-medium mb-2 flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  Invitation created!
                </p>
                <p className="text-xs text-muted-foreground mb-3">
                  Share this link with the invitee. If SMTP is configured, an
                  email was also sent.
                </p>
                <div className="flex gap-2">
                  <Input
                    value={lastInviteLink}
                    readOnly
                    className="bg-background text-xs font-mono"
                  />
                  <Button variant="outline" size="sm" onClick={handleCopyLink} className="shrink-0 gap-1">
                    <Copy className="h-3.5 w-3.5" /> Copy
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setInviteOpen(false)}>
                  Done
                </Button>
                <Button onClick={() => { setLastInviteLink(null); }}>
                  Invite another
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Email address</label>
                <Input
                  type="email"
                  placeholder="colleague@company.com"
                  value={inviteEmail}
                  onChange={(e) => setInviteEmail(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Role</label>
                <Select
                  value={inviteRoleId}
                  onChange={(e) => setInviteRoleId(e.target.value)}
                  options={roles.map((r) => ({ value: r.id, label: r.name }))}
                  placeholder="Select a role"
                />
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setInviteOpen(false)}>
                  Cancel
                </Button>
                <Button
                  onClick={handleInvite}
                  disabled={!inviteEmail || !inviteRoleId || inviteSending}
                  className="gap-2"
                >
                  <Send className="h-4 w-4" />
                  {inviteSending ? "Sending..." : "Send Invitation"}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Assign Role Dialog */}
      <Dialog open={roleDialogOpen} onOpenChange={setRoleDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Assign role</DialogTitle>
            <DialogDescription>
              Select a role to assign to this user.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Role</label>
              <Select
                value={roleDialogRoleId}
                onChange={(e) => setRoleDialogRoleId(e.target.value)}
                options={roles.map((r) => ({ value: r.id, label: r.name }))}
                placeholder="Select a role"
              />
            </div>
            <DialogFooter>
              <Button variant="outline" onClick={() => setRoleDialogOpen(false)}>
                Cancel
              </Button>
              <Button
                onClick={handleAssignRole}
                disabled={!roleDialogRoleId || roleAssigning}
              >
                {roleAssigning ? "Assigning..." : "Assign"}
              </Button>
            </DialogFooter>
          </div>
        </DialogContent>
      </Dialog>

      {/* Create User Dialog */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create a new user</DialogTitle>
            <DialogDescription>
              {!createdPassword
                ? "Create a user account directly. A secure password will be generated automatically."
                : "User created successfully. Copy the password below — it will not be shown again."}
            </DialogDescription>
          </DialogHeader>

          {createdPassword ? (
            <div className="space-y-4">
              <div className="rounded-lg border bg-muted/50 p-4">
                <p className="text-sm font-medium mb-2 flex items-center gap-2">
                  <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  User created!
                </p>
                <p className="text-xs text-muted-foreground mb-1">
                  Account: <span className="font-medium text-foreground">{createdUserEmail}</span>
                </p>
                <p className="text-xs text-muted-foreground mb-3">
                  Share the password below securely. The user should change it after first login.
                </p>
                <div className="space-y-2">
                  <label className="text-xs font-medium flex items-center gap-1.5">
                    <KeyRound className="h-3.5 w-3.5" /> Generated password
                  </label>
                  <div className="flex gap-2">
                    <Input
                      value={createdPassword}
                      readOnly
                      className="bg-background text-sm font-mono"
                    />
                    <Button variant="outline" size="sm" onClick={handleCopyPassword} className="shrink-0 gap-1">
                      <Copy className="h-3.5 w-3.5" /> Copy
                    </Button>
                  </div>
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setCreateOpen(false)}>
                  Done
                </Button>
                <Button onClick={() => { setCreatedPassword(null); }}>
                  Create another
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Full name</label>
                <Input
                  type="text"
                  placeholder="Jane Doe"
                  value={createName}
                  onChange={(e) => setCreateName(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Email address</label>
                <Input
                  type="email"
                  placeholder="jane@company.com"
                  value={createEmail}
                  onChange={(e) => setCreateEmail(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Role</label>
                <Select
                  value={createRoleId}
                  onChange={(e) => setCreateRoleId(e.target.value)}
                  options={roles.map((r) => ({ value: r.id, label: r.name }))}
                  placeholder="Select a role"
                />
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setCreateOpen(false)}>
                  Cancel
                </Button>
                <Button
                  onClick={handleCreateUser}
                  disabled={!createEmail || !createName || !createRoleId || creating}
                  className="gap-2"
                >
                  <Plus className="h-4 w-4" />
                  {creating ? "Creating..." : "Create User"}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </AppLayout>
  );
}
