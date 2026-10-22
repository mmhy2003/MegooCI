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
  ShieldOff,
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
import { ProjectAssignmentsEditor } from "@/components/ProjectAssignmentsEditor";
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { AccessKindCards, type AccessChoice } from "@/components/users/AccessKindCards";
import { InvitationsTab } from "@/components/users/InvitationsTab";
import { RolesTab } from "@/components/users/RolesTab";
import { adminRoleId, memberRoleId } from "@/components/users/access";
import { cn } from "@/lib/utils";
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


type PageTab = "users" | "invitations" | "roles";
const PAGE_TABS: PageTab[] = ["users", "invitations", "roles"];

export default function AdminUsersPage() {
  return (
    <React.Suspense fallback={null}>
      <AdminUsersPageInner />
    </React.Suspense>
  );
}

function AdminUsersPageInner() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const tabParam = searchParams.get("tab") as PageTab | null;
  const tab: PageTab = tabParam && PAGE_TABS.includes(tabParam) ? tabParam : "users";
  const setTab = (next: PageTab) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("tab", next);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };
  const { user: currentUser } = useAuthStore();
  const confirm = useConfirm();

  const [users, setUsers] = React.useState<UserDetail[]>([]);
  const [roles, setRoles] = React.useState<Role[]>([]);
  const [invites, setInvites] = React.useState<Invite[]>([]);
  const [loading, setLoading] = React.useState(true);

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [inviteEmail, setInviteEmail] = React.useState("");
  const [inviteKind, setInviteKind] = React.useState<AccessChoice>("member");
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
  const [createUserType, setCreateUserType] = React.useState<"admin" | "member">("member");
  const [creating, setCreating] = React.useState(false);
  const [createdPassword, setCreatedPassword] = React.useState<string | null>(null);
  const [createdUserEmail, setCreatedUserEmail] = React.useState("");
  const [createdUserId, setCreatedUserId] = React.useState<string | null>(null);

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
    // Members get no role row on accept; the viewer id is only the
    // placeholder the API requires.
    const roleId = inviteKind === "administrator" ? adminRoleId(roles) : memberRoleId(roles);
    if (!inviteEmail || !roleId) return;
    setInviteSending(true);
    try {
      const result: InviteCreated = await invitesApi.create({
        email: inviteEmail,
        role_id: roleId,
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

  const handleResetTwoStep = async (u: UserDetail) => {
    const ok = await confirm({
      title: "Reset two-step verification?",
      description: `This removes the authenticator app, passkeys and recovery codes of ${u.name} (${u.email}). They sign in with their password alone until they set it up again; where it is required, they are asked to at their next sign-in.`,
      confirmText: "Reset",
      tone: "warning",
    });
    if (!ok) return;
    try {
      await usersApi.resetTwoStep(u.id);
      toast.success(`Two-step verification reset for ${u.name}`);
      loadData();
    } catch (e: unknown) {
      const msg =
        (e as { body?: { detail?: string } })?.body?.detail ||
        "Failed to reset two-step verification";
      toast.error(msg);
    }
  };

  const handleDeleteUser = async (u: UserDetail) => {
    const ok = await confirm({
      title: "Delete user permanently?",
      description: `This will permanently delete ${u.name} (${u.email}) and remove all their role assignments. This action cannot be undone.`,
      confirmText: "Delete",
      tone: "destructive",
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
    // For admin type, role_id isn't strictly needed but API requires it —
    // use any non-admin role as the initial assignment, then promote to admin.
    const nonAdminRole = roles.find((r) => r.name !== "admin");
    const effectiveRoleId =
      createUserType === "admin"
        ? (nonAdminRole?.id ?? createRoleId)
        : createRoleId;
    if (!createEmail || !createName || !effectiveRoleId) return;
    setCreating(true);
    try {
      const result: UserCreated = await usersApi.create({
        email: createEmail,
        name: createName,
        role_id: effectiveRoleId,
      });
      if (createUserType === "admin") {
        await usersApi.update(result.id, { is_admin: true });
      }
      toast.success(`User ${createEmail} created`);
      setCreatedPassword(result.generated_password);
      setCreatedUserEmail(createEmail);
      setCreatedUserId(result.id);
      setCreateEmail("");
      setCreateName("");
      setCreateRoleId("");
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
      <div className="mx-auto max-w-6xl space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold tracking-tight sm:text-2xl">Users</h1>
            <p className="text-sm text-muted-foreground">
              Manage who can sign in and what they can reach.
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => { setCreateOpen(true); setCreatedPassword(null); setCreatedUserId(null); setCreateUserType("member"); }}
              className="gap-2"
            >
              <Plus className="h-4 w-4" />
              Create user
            </Button>
            <Button onClick={() => { setInviteOpen(true); setLastInviteLink(null); }} className="gap-2">
              <UserPlus className="h-4 w-4" />
              Invite member
            </Button>
          </div>
        </div>

        <div role="tablist" className="flex gap-1 border-b">
          {PAGE_TABS.map((t) => {
            const count =
              t === "users" ? users.length
              : t === "invitations" ? invites.filter((i) => i.status === "pending").length
              : null;
            return (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={cn(
                  "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                  tab === t
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {t === "users" ? "Users" : t === "invitations" ? "Invitations" : "Roles"}
                {!loading && count !== null && (
                  <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">
                    {count}
                  </span>
                )}
              </button>
            );
          })}
        </div>

        {tab === "users" && (
          <>
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
                        <Badge
                          variant={u.two_step_enabled ? "success" : "pending"}
                          className="text-[10px]"
                          title="Two-step verification"
                        >
                          {u.two_step_enabled ? "Two-step on" : "Two-step off"}
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground truncate">{u.email}</p>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        {u.roles.filter((r) => r.scope_type === "global").map((r) => (
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
                          </Badge>
                        ))}
                        {u.roles.filter((r) => r.scope_type === "global").length === 0 && !u.is_admin && (
                          <span className="text-xs text-muted-foreground italic">No global role</span>
                        )}
                      </div>
                      <div className="mt-2">
                        <p className="mb-1 text-xs font-medium text-muted-foreground">Project access</p>
                        <ProjectAssignmentsEditor user={u} onChanged={loadData} />
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
                          onClick={() => handleResetTwoStep(u)}
                          disabled={!u.two_step_enabled || u.id === currentUser?.id}
                        >
                          <ShieldOff className="mr-2 h-4 w-4" /> Reset two-step verification
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

          </>
        )}
        {tab === "invitations" && (
          <InvitationsTab invites={invites} loading={loading} onChanged={loadData} />
        )}
        {tab === "roles" && <RolesTab roles={roles} loading={loading} />}
      </div>

      {/* Invite Dialog */}
      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Invite a new member</DialogTitle>
            <DialogDescription>
              Send an invitation email with a one-time sign-up link.
              {!lastInviteLink && " They join as an Administrator or a Member."}
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
                <label className="text-sm font-medium">Access</label>
                <AccessKindCards
                  value={inviteKind}
                  onChange={setInviteKind}
                  memberText="Only the projects you give them after they join."
                />
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setInviteOpen(false)}>
                  Cancel
                </Button>
                <Button
                  onClick={handleInvite}
                  disabled={!inviteEmail || inviteSending}
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
              {createUserType === "member" && createdUserId && (() => {
                const createdUser = users.find((u) => u.id === createdUserId);
                return createdUser ? (
                  <div className="space-y-1.5">
                    <p className="text-sm font-medium">Assign project access</p>
                    <p className="text-xs text-muted-foreground">
                      This member has no global role. Assign project-scoped access now or do it later via the Users table.
                    </p>
                    <ProjectAssignmentsEditor user={createdUser} onChanged={loadData} />
                  </div>
                ) : null;
              })()}
              <DialogFooter>
                <Button variant="outline" onClick={() => { setCreateOpen(false); setCreatedUserId(null); }}>
                  Done
                </Button>
                <Button onClick={() => { setCreatedPassword(null); setCreatedUserId(null); }}>
                  Create another
                </Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Account type</label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setCreateUserType("member")}
                    className={`flex-1 rounded-md border px-3 py-2 text-sm transition-colors ${
                      createUserType === "member"
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-input text-muted-foreground hover:border-primary/50"
                    }`}
                  >
                    <div className="font-medium">Member</div>
                    <div className="text-xs opacity-70">Project-scoped access</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => setCreateUserType("admin")}
                    className={`flex-1 rounded-md border px-3 py-2 text-sm transition-colors ${
                      createUserType === "admin"
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-input text-muted-foreground hover:border-primary/50"
                    }`}
                  >
                    <div className="font-medium">Admin</div>
                    <div className="text-xs opacity-70">Full system access</div>
                  </button>
                </div>
              </div>
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
              {createUserType === "member" && (
                <div className="space-y-2">
                  <label className="text-sm font-medium">Initial role</label>
                  <Select
                    value={createRoleId}
                    onChange={(e) => setCreateRoleId(e.target.value)}
                    options={roles.filter((r) => r.name !== "admin").map((r) => ({ value: r.id, label: r.name }))}
                    placeholder="Select a role"
                  />
                  <p className="text-xs text-muted-foreground">
                    Assign project-scoped access after creation via the Users table.
                  </p>
                </div>
              )}
              <DialogFooter>
                <Button variant="outline" onClick={() => setCreateOpen(false)}>
                  Cancel
                </Button>
                <Button
                  onClick={handleCreateUser}
                  disabled={
                    !createEmail ||
                    !createName ||
                    roles.length === 0 ||
                    (createUserType === "member" && !createRoleId) ||
                    creating
                  }
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
