"use client";

import * as React from "react";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import {
  User,
  Monitor,
  Sparkles,
  CheckCircle2,
  AlertTriangle,
  XCircle,
  KeyRound,
  HardDrive,
  Shield,
  Package,
  Palette,
  Lock,
  Plus,
  Copy,
  Trash2,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useAuthStore } from "@/lib/auth";
import { useTheme } from "@/components/providers";
import { ThemeToggle } from "@/components/theme-toggle";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { authApi, systemApi, apiTokensApi, type AiInfo, type SystemInfo, type ApiToken, type ApiTokenCreated } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog";

function AiStatusBadge({ ai }: { ai: AiInfo }) {
  if (ai.status === "ready") {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" /> Ready
      </Badge>
    );
  }
  if (ai.status === "disabled") {
    return (
      <Badge variant="cancelled" className="gap-1">
        <XCircle className="h-3 w-3" /> Disabled
      </Badge>
    );
  }
  if (ai.status === "missing_api_key") {
    return (
      <Badge variant="failed" className="gap-1">
        <KeyRound className="h-3 w-3" /> Missing API key
      </Badge>
    );
  }
  return (
    <Badge variant="failed" className="gap-1">
      <AlertTriangle className="h-3 w-3" /> Misconfigured
    </Badge>
  );
}

function ConfigRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5 rounded-lg border px-3 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-4">
      <span className="text-muted-foreground">{label}</span>
      <div className="break-all text-left sm:text-right">{children}</div>
    </div>
  );
}

export default function SettingsPage() {
  const { user } = useAuthStore();
  const { theme, resolvedTheme } = useTheme();
  const [info, setInfo] = React.useState<SystemInfo | null>(null);
  const [loading, setLoading] = React.useState(true);

  const [currentPw, setCurrentPw] = React.useState("");
  const [newPw, setNewPw] = React.useState("");
  const [confirmPw, setConfirmPw] = React.useState("");
  const [changingPw, setChangingPw] = React.useState(false);
  const confirm = useConfirm();

  // API Tokens state
  const [tokens, setTokens] = React.useState<ApiToken[]>([]);
  const [tokensLoading, setTokensLoading] = React.useState(true);
  const [showCreateToken, setShowCreateToken] = React.useState(false);
  const [newTokenName, setNewTokenName] = React.useState("");
  const [newTokenExpiry, setNewTokenExpiry] = React.useState<string>("");
  const [creatingToken, setCreatingToken] = React.useState(false);
  const [createdToken, setCreatedToken] = React.useState<ApiTokenCreated | null>(null);

  React.useEffect(() => {
    systemApi
      .info()
      .then(setInfo)
      .catch(() => toast.error("Failed to load system configuration"))
      .finally(() => setLoading(false));

    apiTokensApi
      .list()
      .then(setTokens)
      .catch(() => {})
      .finally(() => setTokensLoading(false));
  }, []);

  async function handleChangePassword(e: React.FormEvent) {
    e.preventDefault();
    if (!currentPw || !newPw || !confirmPw) {
      toast.error("Please fill in all password fields");
      return;
    }
    if (newPw !== confirmPw) {
      toast.error("New passwords do not match");
      return;
    }
    if (newPw.length < 8) {
      toast.error("New password must be at least 8 characters");
      return;
    }
    setChangingPw(true);
    try {
      await authApi.changePassword({
        current_password: currentPw,
        new_password: newPw,
      });
      toast.success("Password updated successfully");
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to change password";
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ?? message;
      toast.error(detail);
    } finally {
      setChangingPw(false);
    }
  }

  return (
    <AppLayout>
      <div className="mx-auto max-w-3xl space-y-8">
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            Settings
          </h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            Manage your profile and view the current system configuration.
          </p>
        </div>

        {/* Profile */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="h-5 w-5" />
              Profile
            </CardTitle>
            <CardDescription>
              Your personal account information.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Full name</label>
              <Input value={user?.name || ""} readOnly className="bg-muted" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Email</label>
              <Input value={user?.email || ""} readOnly className="bg-muted" />
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Role</label>
              <div>
                <Badge variant="secondary">
                  {user?.role || (user?.is_admin ? "admin" : "user")}
                </Badge>
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium">Account status</label>
              <div>
                <Badge variant={user?.is_active ? "success" : "failed"}>
                  {user?.is_active ? "Active" : "Inactive"}
                </Badge>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Change Password */}
        {user?.auth_provider === "local" && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Lock className="h-5 w-5" />
                Change Password
              </CardTitle>
              <CardDescription>
                Update your account password. You&apos;ll need your current
                password to set a new one.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleChangePassword} className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    Current password
                  </label>
                  <Input
                    type="password"
                    placeholder="••••••••"
                    value={currentPw}
                    onChange={(e) => setCurrentPw(e.target.value)}
                    autoComplete="current-password"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">New password</label>
                  <Input
                    type="password"
                    placeholder="••••••••"
                    value={newPw}
                    onChange={(e) => setNewPw(e.target.value)}
                    autoComplete="new-password"
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    Confirm new password
                  </label>
                  <Input
                    type="password"
                    placeholder="••••••••"
                    value={confirmPw}
                    onChange={(e) => setConfirmPw(e.target.value)}
                    autoComplete="new-password"
                  />
                </div>
                <Button type="submit" disabled={changingPw}>
                  {changingPw ? "Updating..." : "Update password"}
                </Button>
              </form>
            </CardContent>
          </Card>
        )}

        {/* Appearance */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Palette className="h-5 w-5" />
              Appearance
            </CardTitle>
            <CardDescription>
              Choose how MegooCI looks. Selecting{" "}
              <span className="font-medium text-foreground">System</span>{" "}
              follows your operating system&apos;s light / dark preference
              automatically.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <label className="text-sm font-medium">Theme</label>
              <ThemeToggle />
            </div>
            <p className="text-xs text-muted-foreground">
              Current mode:{" "}
              <span className="font-medium text-foreground">
                {resolvedTheme === "dark" ? "Dark" : "Light"}
              </span>
              {theme === "system" && " (auto-detected from system)"}
            </p>
          </CardContent>
        </Card>

        {/* API Tokens */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <KeyRound className="h-5 w-5" />
                  API Tokens
                </CardTitle>
                <CardDescription>
                  Personal access tokens for API authentication. Use these to
                  authenticate with the MegooCI API, download artifacts, and
                  automate workflows.
                </CardDescription>
              </div>
              <Button
                size="sm"
                onClick={() => setShowCreateToken(true)}
                className="gap-1.5"
              >
                <Plus className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Create token</span>
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {tokensLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 2 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : tokens.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                No API tokens yet. Create one to authenticate scripts and CI/CD
                integrations.
              </p>
            ) : (
              <div className="space-y-2">
                {tokens.map((t) => (
                  <div
                    key={t.id}
                    className="flex items-center justify-between rounded-lg border px-3 py-3"
                  >
                    <div className="space-y-0.5">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{t.name}</span>
                        <Badge
                          variant={t.is_active ? "success" : "cancelled"}
                          className="text-[10px]"
                        >
                          {t.is_active ? "Active" : "Revoked"}
                        </Badge>
                      </div>
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                        <span>
                          <code>{t.token_hint}…</code>
                        </span>
                        {t.expires_at && (
                          <span>
                            Expires{" "}
                            {formatDistanceToNow(new Date(t.expires_at), {
                              addSuffix: true,
                            })}
                          </span>
                        )}
                        {t.last_used_at && (
                          <span>
                            Last used{" "}
                            {formatDistanceToNow(new Date(t.last_used_at), {
                              addSuffix: true,
                            })}
                          </span>
                        )}
                        <span>
                          Created{" "}
                          {formatDistanceToNow(new Date(t.created_at), {
                            addSuffix: true,
                          })}
                        </span>
                      </div>
                    </div>
                    {t.is_active && (
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-8 w-8 text-destructive"
                        onClick={async () => {
                          const ok = await confirm({
                            title: "Revoke this token?",
                            description: (
                              <>
                                Token <strong>{t.name}</strong> (
                                <code>{t.token_hint}…</code>) will be
                                permanently deactivated. Any scripts using it
                                will stop working.
                              </>
                            ),
                            confirmText: "Revoke",
                            tone: "destructive",
                          });
                          if (!ok) return;
                          try {
                            await apiTokensApi.revoke(t.id);
                            setTokens((prev) =>
                              prev.map((x) =>
                                x.id === t.id ? { ...x, is_active: false } : x,
                              ),
                            );
                            toast.success("Token revoked");
                          } catch {
                            toast.error("Failed to revoke token");
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Create Token Dialog */}
        <Dialog
          open={showCreateToken || !!createdToken}
          onOpenChange={(open) => {
            if (!open) {
              setShowCreateToken(false);
              setCreatedToken(null);
              setNewTokenName("");
              setNewTokenExpiry("");
            }
          }}
        >
          <DialogContent>
            {createdToken ? (
              <>
                <DialogHeader>
                  <DialogTitle>Token created</DialogTitle>
                  <DialogDescription>
                    Copy this token now — you won&apos;t be able to see it again.
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-3">
                  <div className="flex items-center gap-2">
                    <Input
                      readOnly
                      value={createdToken.token}
                      className="font-mono text-xs"
                    />
                    <Button
                      variant="outline"
                      size="icon"
                      className="shrink-0"
                      onClick={() => {
                        navigator.clipboard.writeText(createdToken.token);
                        toast.success("Copied to clipboard");
                      }}
                    >
                      <Copy className="h-4 w-4" />
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    Use this token as a Bearer token in the{" "}
                    <code>Authorization</code> header, e.g.:{" "}
                    <code className="block mt-1 break-all rounded bg-muted px-2 py-1">
                      curl -H &quot;Authorization: Bearer {createdToken.token_hint}…&quot;
                    </code>
                  </p>
                </div>
                <DialogFooter>
                  <Button
                    onClick={() => {
                      setCreatedToken(null);
                      setShowCreateToken(false);
                    }}
                  >
                    Done
                  </Button>
                </DialogFooter>
              </>
            ) : (
              <>
                <DialogHeader>
                  <DialogTitle>Create API token</DialogTitle>
                  <DialogDescription>
                    The token will inherit your current role&apos;s permissions.
                  </DialogDescription>
                </DialogHeader>
                <form
                  onSubmit={async (e) => {
                    e.preventDefault();
                    if (!newTokenName.trim()) {
                      toast.error("Token name is required");
                      return;
                    }
                    setCreatingToken(true);
                    try {
                      const result = await apiTokensApi.create({
                        name: newTokenName.trim(),
                        expires_in_days: newTokenExpiry
                          ? parseInt(newTokenExpiry, 10)
                          : null,
                      });
                      setCreatedToken(result);
                      setTokens((prev) => [
                        {
                          id: result.id,
                          name: result.name,
                          token_hint: result.token_hint,
                          scopes: result.scopes,
                          expires_at: result.expires_at,
                          is_active: result.is_active,
                          last_used_at: result.last_used_at,
                          created_at: result.created_at,
                        },
                        ...prev,
                      ]);
                      setNewTokenName("");
                      setNewTokenExpiry("");
                    } catch {
                      toast.error("Failed to create token");
                    } finally {
                      setCreatingToken(false);
                    }
                  }}
                  className="space-y-4"
                >
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Token name</label>
                    <Input
                      placeholder="e.g. CI deploy script"
                      value={newTokenName}
                      onChange={(e) => setNewTokenName(e.target.value)}
                      autoFocus
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">
                      Expires in (days)
                    </label>
                    <Input
                      type="number"
                      min={1}
                      max={365}
                      placeholder="Leave empty for no expiry"
                      value={newTokenExpiry}
                      onChange={(e) => setNewTokenExpiry(e.target.value)}
                    />
                  </div>
                  <DialogFooter>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setShowCreateToken(false)}
                    >
                      Cancel
                    </Button>
                    <Button type="submit" disabled={creatingToken}>
                      {creatingToken ? "Creating…" : "Create token"}
                    </Button>
                  </DialogFooter>
                </form>
              </>
            )}
          </DialogContent>
        </Dialog>

        {/* AI Configuration */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              AI Configuration
            </CardTitle>
            <CardDescription>
              Detected from the controller&apos;s environment.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : (
              <>
                <div className="space-y-3">
                  <ConfigRow label="Status">
                    <AiStatusBadge ai={info.ai} />
                  </ConfigRow>
                  <ConfigRow label="Enabled (MEGOOCI_AI_ENABLED)">
                    <Badge
                      variant={info.ai.enabled ? "success" : "cancelled"}
                    >
                      {info.ai.enabled ? "Yes" : "No"}
                    </Badge>
                  </ConfigRow>
                  <ConfigRow label="Provider">
                    <code className="rounded bg-muted px-2 py-0.5 text-xs">
                      {info.ai.provider || "—"}
                    </code>
                  </ConfigRow>
                  <ConfigRow label="Model">
                    <code className="rounded bg-muted px-2 py-0.5 text-xs">
                      {info.ai.model || "—"}
                    </code>
                  </ConfigRow>
                  <ConfigRow label="API Key">
                    <Badge
                      variant={info.ai.has_api_key ? "success" : "cancelled"}
                    >
                      {info.ai.has_api_key ? "Set" : "Not set"}
                    </Badge>
                  </ConfigRow>
                  {info.ai.base_url && (
                    <ConfigRow label="Base URL">
                      <code className="rounded bg-muted px-2 py-0.5 text-xs">
                        {info.ai.base_url}
                      </code>
                    </ConfigRow>
                  )}
                </div>
                <p
                  className={`mt-4 text-xs ${
                    info.ai.status === "ready"
                      ? "text-muted-foreground"
                      : "text-amber-600 dark:text-amber-400"
                  }`}
                >
                  {info.ai.status_detail}
                </p>
                {info.ai.status !== "ready" && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Update <code>MEGOOCI_AI_*</code> in your{" "}
                    <code>.env</code> file and restart the backend to change
                    these values.
                  </p>
                )}
              </>
            )}
          </CardContent>
        </Card>

        {/* System */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Monitor className="h-5 w-5" />
              System
            </CardTitle>
            <CardDescription>
              Current runtime configuration from the backend.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : (
              <div className="space-y-3">
                <ConfigRow label="Version">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.version}
                  </code>
                </ConfigRow>
                <ConfigRow label="Public URL">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.public_url}
                  </code>
                </ConfigRow>
                <ConfigRow label="API URL (browser)">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {process.env.NEXT_PUBLIC_API_URL || "/api"}
                  </code>
                </ConfigRow>
                <ConfigRow label="Log level">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.log_level}
                  </code>
                </ConfigRow>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Authentication */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Shield className="h-5 w-5" />
              Authentication
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : (
              <div className="space-y-3">
                <ConfigRow label="Public signup">
                  <Badge
                    variant={info.auth.signup_enabled ? "success" : "cancelled"}
                  >
                    {info.auth.signup_enabled ? "Enabled" : "Disabled"}
                  </Badge>
                </ConfigRow>
                <ConfigRow label="Default role for new signups">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.auth.default_role}
                  </code>
                </ConfigRow>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Storage */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HardDrive className="h-5 w-5" />
              Storage
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : (
              <div className="space-y-3">
                <ConfigRow label="Storage root">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.storage.storage_root}
                  </code>
                </ConfigRow>
                <ConfigRow label="Retention (builds)">
                  <span>{info.storage.retention_builds} builds</span>
                </ConfigRow>
                <ConfigRow label="Retention (days)">
                  <span>{info.storage.retention_days} days</span>
                </ConfigRow>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Registry */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Package className="h-5 w-5" />
              Container Registry
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading || !info ? (
              <div className="space-y-3">
                <Skeleton className="h-12 w-full" />
                <Skeleton className="h-12 w-full" />
              </div>
            ) : (
              <div className="space-y-3">
                <ConfigRow label="Status">
                  <Badge
                    variant={info.registry.enabled ? "success" : "cancelled"}
                  >
                    {info.registry.enabled ? "Enabled" : "Disabled"}
                  </Badge>
                </ConfigRow>
                <ConfigRow label="Host">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.registry.host}
                  </code>
                </ConfigRow>
                <ConfigRow label="Storage Path">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.registry.storage_path}
                  </code>
                </ConfigRow>
                <ConfigRow label="Max Upload">
                  <span className="text-sm">
                    {info.registry.max_upload_mb} MB
                  </span>
                </ConfigRow>
                <ConfigRow label="GC Schedule">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.registry.gc_cron}
                  </code>
                </ConfigRow>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
