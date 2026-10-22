"use client";

import * as React from "react";
import { toast } from "sonner";
import { CheckCircle2, Copy, KeyRound, Plus, Send, UserPlus } from "lucide-react";
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
import { useRouter, usePathname, useSearchParams } from "next/navigation";
import { AccessKindCards, type AccessChoice } from "@/components/users/AccessKindCards";
import { InvitationsTab } from "@/components/users/InvitationsTab";
import { RolesTab } from "@/components/users/RolesTab";
import { UsersTab } from "@/components/users/UsersTab";
import { ProjectAccessEditor } from "@/components/users/ProjectAccessEditor";
import { adminRoleId, memberRoleId } from "@/components/users/access";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

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

  const [users, setUsers] = React.useState<UserDetail[]>([]);
  const [roles, setRoles] = React.useState<Role[]>([]);
  const [invites, setInvites] = React.useState<Invite[]>([]);
  const [loading, setLoading] = React.useState(true);

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [inviteEmail, setInviteEmail] = React.useState("");
  const [inviteKind, setInviteKind] = React.useState<AccessChoice>("member");
  const [inviteSending, setInviteSending] = React.useState(false);
  const [lastInviteLink, setLastInviteLink] = React.useState<string | null>(null);

  const [createOpen, setCreateOpen] = React.useState(false);
  const [createEmail, setCreateEmail] = React.useState("");
  const [createName, setCreateName] = React.useState("");
  const [createKind, setCreateKind] = React.useState<AccessChoice>("member");
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

  const handleCreateUser = async () => {
    // An Administrator is created with the admin role, which also sets the
    // administrator setting. Members get no role row; the viewer id is only
    // the placeholder the API requires.
    const roleId = createKind === "administrator" ? adminRoleId(roles) : memberRoleId(roles);
    if (!createEmail || !createName || !roleId) return;
    setCreating(true);
    try {
      const result: UserCreated = await usersApi.create({
        email: createEmail,
        name: createName,
        role_id: roleId,
      });
      toast.success(`User ${createEmail} created`);
      setCreatedPassword(result.generated_password);
      setCreatedUserEmail(createEmail);
      setCreatedUserId(result.id);
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
              onClick={() => { setCreateOpen(true); setCreatedPassword(null); setCreatedUserId(null); setCreateKind("member"); }}
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
          <UsersTab users={users} roles={roles} loading={loading} onChanged={loadData} />
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
              {createKind === "member" && createdUserId && (() => {
                const createdUser = users.find((u) => u.id === createdUserId);
                return createdUser ? (
                  <div className="space-y-1.5">
                    <p className="text-xs text-muted-foreground">
                      Give them projects now, or later from their Access tab.
                    </p>
                    <ProjectAccessEditor
                      user={createdUser}
                      roles={roles}
                      users={users}
                      onChanged={loadData}
                    />
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
                <label className="text-sm font-medium">Access</label>
                <AccessKindCards
                  value={createKind}
                  onChange={setCreateKind}
                  memberText="Only the projects you give them, next."
                />
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
