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
  Pencil,
  Save,
  RotateCcw,
  Eye,
  EyeOff,
  Wrench,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useAuthStore } from "@/lib/auth";
import { useTheme } from "@/components/providers";
import { ThemeToggle } from "@/components/theme-toggle";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { authApi, systemApi, apiTokensApi, type AiInfo, type MaintenanceInfo, type SystemInfo, type ApiToken, type ApiTokenCreated, type ApiTokenScope, type AiSettingsUpdate } from "@/lib/api";
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

function MaintenanceCard({
  info,
  loading,
  onUpdated,
}: {
  info: SystemInfo | null;
  loading: boolean;
  onUpdated: (m: MaintenanceInfo) => void;
}) {
  const [saving, setSaving] = React.useState(false);
  const [message, setMessage] = React.useState("");
  const confirm = useConfirm();

  const enabled = info?.maintenance?.enabled ?? false;
  const currentMessage = info?.maintenance?.message ?? "";

  React.useEffect(() => {
    setMessage(currentMessage);
  }, [currentMessage]);

  async function handleToggle(newEnabled: boolean) {
    if (newEnabled) {
      const ok = await confirm({
        title: "Enable Maintenance Mode",
        description:
          "All pending builds will be paused. New builds will still be queued but won't execute until maintenance mode is disabled. Continue?",
        confirmText: "Enable",
      });
      if (!ok) return;
    }

    setSaving(true);
    try {
      const result = await systemApi.setMaintenance({
        enabled: newEnabled,
        message: newEnabled ? message || null : null,
      });
      onUpdated(result);
      toast.success(
        newEnabled
          ? "Maintenance mode enabled — builds are paused"
          : "Maintenance mode disabled — queued builds will resume"
      );
    } catch (err: unknown) {
      toast.error(
        err instanceof Error ? err.message : "Failed to toggle maintenance mode"
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleUpdateMessage() {
    setSaving(true);
    try {
      const result = await systemApi.setMaintenance({
        enabled: true,
        message: message || null,
      });
      onUpdated(result);
      toast.success("Maintenance message updated");
    } catch {
      toast.error("Failed to update message");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card className={enabled ? "border-amber-500/50 dark:border-amber-400/30" : ""}>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Wrench className={`h-5 w-5 ${enabled ? "text-amber-500" : ""}`} />
              Maintenance Mode
            </CardTitle>
            <CardDescription>
              Pause all build execution while performing system maintenance.
            </CardDescription>
          </div>
          <div className="flex items-center gap-3">
            {!loading && info && (
              <>
                <Badge variant={enabled ? "warning" : "success"}>
                  {enabled ? "Active" : "Off"}
                </Badge>
                <button
                  type="button"
                  role="switch"
                  aria-checked={enabled}
                  disabled={saving}
                  onClick={() => handleToggle(!enabled)}
                  className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
                    enabled ? "bg-amber-500" : "bg-muted"
                  }`}
                >
                  <span
                    className={`pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                      enabled ? "translate-x-5" : "translate-x-0"
                    }`}
                  />
                </button>
              </>
            )}
          </div>
        </div>
      </CardHeader>
      {enabled && (
        <CardContent>
          <div className="rounded-lg border border-amber-500/20 bg-amber-500/5 p-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
              <div className="flex-1 space-y-3">
                <p className="text-sm text-amber-700 dark:text-amber-300">
                  Build execution is paused. New builds are queued as{" "}
                  <span className="font-medium">pending</span> and will start
                  automatically once maintenance mode is disabled.
                </p>
                <div className="space-y-1.5">
                  <label className="text-xs font-medium text-muted-foreground">
                    Maintenance message (optional)
                  </label>
                  <div className="flex gap-2">
                    <Input
                      value={message}
                      onChange={(e) => setMessage(e.target.value)}
                      placeholder="e.g. Database migration in progress..."
                      className="h-8 text-xs"
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      className="h-8 shrink-0"
                      disabled={saving || message === currentMessage}
                      onClick={handleUpdateMessage}
                    >
                      Save
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  );
}

const AI_PROVIDERS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "ollama", label: "Ollama" },
  { value: "azure_openai", label: "Azure OpenAI" },
  { value: "custom", label: "Custom (OpenAI-compatible)" },
  { value: "disabled", label: "Disabled" },
];

function AiConfigCard({
  info,
  loading,
  isAdmin,
  onUpdated,
}: {
  info: SystemInfo | null;
  loading: boolean;
  isAdmin: boolean;
  onUpdated: (ai: AiInfo) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [showKey, setShowKey] = React.useState(false);

  // Form state
  const [provider, setProvider] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [model, setModel] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [enabled, setEnabled] = React.useState(true);

  // Sync form state when entering edit mode or when info changes
  function syncForm() {
    if (!info) return;
    setProvider(info.ai.provider || "openai");
    setBaseUrl(info.ai.base_url || "");
    setModel(info.ai.model || "");
    setApiKey(""); // never pre-fill API key for security
    setEnabled(info.ai.enabled);
    setShowKey(false);
  }

  function handleEdit() {
    syncForm();
    setEditing(true);
  }

  function handleCancel() {
    setEditing(false);
    setShowKey(false);
  }

  async function handleSave() {
    setSaving(true);
    try {
      const update: AiSettingsUpdate = {
        enabled,
        provider,
        base_url: baseUrl,
        model,
      };
      // Only send api_key if user actually typed something
      if (apiKey) {
        update.api_key = apiKey;
      }
      const updatedAi = await systemApi.updateAi(update);
      onUpdated(updatedAi);
      setEditing(false);
      setShowKey(false);
      toast.success("AI configuration updated");
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err instanceof Error ? err.message : "Failed to update AI settings");
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              AI Configuration
            </CardTitle>
            <CardDescription>
              {editing
                ? "Configure the LLM provider for the AI pipeline assistant."
                : "Current AI provider configuration."}
            </CardDescription>
          </div>
          {isAdmin && !editing && !loading && info && (
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={handleEdit}
            >
              <Pencil className="h-3.5 w-3.5" />
              Edit
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {loading || !info ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : editing ? (
          /* ─── Edit Mode ─── */
          <div className="space-y-4">
            {/* Enabled */}
            <div className="flex items-center justify-between rounded-lg border px-3 py-3">
              <label className="text-sm text-muted-foreground">Enabled</label>
              <button
                type="button"
                role="switch"
                aria-checked={enabled}
                onClick={() => setEnabled(!enabled)}
                className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  enabled ? "bg-primary" : "bg-muted"
                }`}
              >
                <span
                  className={`pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                    enabled ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>

            {/* Provider */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Provider</label>
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                {AI_PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                Select &ldquo;Custom&rdquo; for any OpenAI-compatible API (vLLM,
                LiteLLM, LM Studio, etc.)
              </p>
            </div>

            {/* Base URL */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Base URL</label>
              <Input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={
                  provider === "ollama"
                    ? "http://localhost:11434/v1"
                    : provider === "openai"
                      ? "https://api.openai.com/v1 (default)"
                      : "https://your-api-endpoint/v1"
                }
              />
              <p className="text-xs text-muted-foreground">
                Leave empty for the provider&apos;s default endpoint. Set this to
                point to a different OpenAI-compatible server.
              </p>
            </div>

            {/* Model */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Model</label>
              <Input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={
                  provider === "openai"
                    ? "gpt-4o-mini"
                    : provider === "anthropic"
                      ? "claude-sonnet-4-5"
                      : provider === "ollama"
                        ? "llama3.2"
                        : "model-name"
                }
              />
            </div>

            {/* API Key */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">API Key</label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Input
                    type={showKey ? "text" : "password"}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={
                      info.ai.has_api_key
                        ? "••••••••  (leave empty to keep current)"
                        : "sk-..."
                    }
                    autoComplete="off"
                  />
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9 shrink-0"
                  onClick={() => setShowKey(!showKey)}
                >
                  {showKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {info.ai.has_api_key
                  ? "An API key is already set. Leave this empty to keep it, or enter a new one to replace it."
                  : "Required for cloud providers (OpenAI, Anthropic, Azure). Not needed for local Ollama."}
              </p>
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                onClick={handleCancel}
                disabled={saving}
              >
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                Cancel
              </Button>
              <Button onClick={handleSave} disabled={saving}>
                <Save className="mr-1.5 h-3.5 w-3.5" />
                {saving ? "Saving…" : "Save changes"}
              </Button>
            </div>
          </div>
        ) : (
          /* ─── Read-only Mode ─── */
          <>
            <div className="space-y-3">
              <ConfigRow label="Status">
                <AiStatusBadge ai={info.ai} />
              </ConfigRow>
              <ConfigRow label="Enabled">
                <Badge
                  variant={info.ai.enabled ? "success" : "cancelled"}
                >
                  {info.ai.enabled ? "Yes" : "No"}
                </Badge>
              </ConfigRow>
              <ConfigRow label="Provider">
                <code className="rounded bg-muted px-2 py-0.5 text-xs">
                  {AI_PROVIDERS.find((p) => p.value === info.ai.provider)?.label || info.ai.provider || "—"}
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
          </>
        )}
      </CardContent>
    </Card>
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
  const [scopeCatalog, setScopeCatalog] = React.useState<ApiTokenScope[]>([]);
  const [newTokenScope, setNewTokenScope] = React.useState<string>("full_access");

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

    apiTokensApi
      .scopes()
      .then(setScopeCatalog)
      .catch(() => {});
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

  const [profileName, setProfileName] = React.useState("");
  const [profileEmail, setProfileEmail] = React.useState("");
  const [savingProfile, setSavingProfile] = React.useState(false);
  const [profileDirty, setProfileDirty] = React.useState(false);

  // Sync local profile state when user loads.
  React.useEffect(() => {
    if (user) {
      setProfileName(user.name || "");
      setProfileEmail(user.email || "");
    }
  }, [user]);

  // Track dirty state.
  React.useEffect(() => {
    if (!user) return;
    setProfileDirty(
      profileName.trim() !== (user.name || "") ||
      profileEmail.trim().toLowerCase() !== (user.email || ""),
    );
  }, [profileName, profileEmail, user]);

  async function handleSaveProfile(e: React.FormEvent) {
    e.preventDefault();
    if (!user) return;
    const updates: { name?: string; email?: string } = {};
    const trimmedName = profileName.trim();
    const trimmedEmail = profileEmail.trim().toLowerCase();
    if (trimmedName !== user.name) updates.name = trimmedName;
    if (trimmedEmail !== user.email) updates.email = trimmedEmail;
    if (!Object.keys(updates).length) return;

    if (!trimmedName) {
      toast.error("Name cannot be empty");
      return;
    }

    setSavingProfile(true);
    try {
      const updatedUser = await authApi.updateProfile(updates);
      // Refresh the auth store with updated user data.
      useAuthStore.setState({ user: updatedUser });
      toast.success("Profile updated");
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err instanceof Error ? err.message : "Failed to update profile");
      toast.error(detail);
    } finally {
      setSavingProfile(false);
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
          <CardContent>
            <form onSubmit={handleSaveProfile} className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Full name</label>
                <Input
                  value={profileName}
                  onChange={(e) => setProfileName(e.target.value)}
                  placeholder="Your name"
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Email</label>
                <Input
                  type="email"
                  value={profileEmail}
                  onChange={(e) => setProfileEmail(e.target.value)}
                  placeholder="you@example.com"
                />
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
              <Button type="submit" disabled={!profileDirty || savingProfile}>
                {savingProfile ? "Saving..." : "Save changes"}
              </Button>
            </form>
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
                        <Badge variant="outline" className="text-[10px]">
                          {t.scope.label}
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
              setNewTokenScope("full_access");
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
                    Choose what this token can do. Access is always capped by
                    your role&apos;s permissions.
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
                        scope: newTokenScope,
                      });
                      setCreatedToken(result);
                      setTokens((prev) => [
                        {
                          id: result.id,
                          name: result.name,
                          token_hint: result.token_hint,
                          scopes: result.scopes,
                          scope: result.scope,
                          expires_at: result.expires_at,
                          is_active: result.is_active,
                          last_used_at: result.last_used_at,
                          created_at: result.created_at,
                        },
                        ...prev,
                      ]);
                      setNewTokenName("");
                      setNewTokenExpiry("");
                      setNewTokenScope("full_access");
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
                    <label className="text-sm font-medium">Scope</label>
                    <select
                      value={newTokenScope}
                      onChange={(e) => setNewTokenScope(e.target.value)}
                      className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                    >
                      {scopeCatalog.map((s) => (
                        <option key={s.key} value={s.key}>
                          {s.label}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-muted-foreground">
                      {scopeCatalog.find((s) => s.key === newTokenScope)?.description}
                    </p>
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

        {/* Maintenance Mode */}
        {user?.is_admin && (
          <MaintenanceCard
            info={info}
            loading={loading}
            onUpdated={(m) => {
              if (info) setInfo({ ...info, maintenance: m });
            }}
          />
        )}

        {/* AI Configuration */}
        <AiConfigCard
          info={info}
          loading={loading}
          isAdmin={!!user?.is_admin}
          onUpdated={(ai) => {
            if (info) setInfo({ ...info, ai });
          }}
        />

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
