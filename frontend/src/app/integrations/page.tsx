"use client";

import * as React from "react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  Bell,
  CheckCircle2,
  ExternalLink,
  Eye,
  EyeOff,
  Github,
  GitBranch,
  GitlabIcon,
  Globe,
  Hash,
  KeyRound,
  Mail,
  MessageSquare,
  Pencil,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  Send,
  Trash2,
  XCircle,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { RequireAdmin } from "@/components/require-permission";
import {
  gitConnectionsApi,
  notificationChannelsApi,
  type GitConnection,
  type GitConnectionTestResult,
  type GitProviderType,
  type NotificationChannel,
  type NotificationChannelTestResult,
  type NotificationChannelType,
} from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface ProviderMeta {
  label: string;
  defaultBaseUrl: string;
  icon: React.ElementType;
  tokenLabel: string;
  tokenPlaceholder: string;
  /** Required / recommended OAuth-style scopes, rendered as chips. */
  scopes: string[];
  /** Extra human-readable hint shown under the token input. */
  tokenHint: string;
}

const PROVIDER_META: Record<GitProviderType, ProviderMeta> = {
  github: {
    label: "GitHub",
    defaultBaseUrl: "https://api.github.com",
    icon: Github,
    tokenLabel: "Personal access token",
    tokenPlaceholder: "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
    scopes: ["repo", "read:org"],
    tokenHint:
      "Use a classic or fine-grained PAT. The 'repo' scope is required for private repositories; 'read:org' is only needed if the repo lives under an organisation.",
  },
  gitlab: {
    label: "GitLab",
    defaultBaseUrl: "https://gitlab.com",
    icon: GitlabIcon,
    tokenLabel: "Personal access token",
    tokenPlaceholder: "glpat-xxxxxxxxxxxxxxxxxxxx",
    scopes: ["api", "read_repository"],
    tokenHint:
      "Create at GitLab \u2192 Preferences \u2192 Access Tokens. 'api' grants read access for the repository picker; 'read_repository' is used for clone operations.",
  },
  generic: {
    label: "Generic Git",
    defaultBaseUrl: "",
    icon: Globe,
    tokenLabel: "Credentials",
    tokenPlaceholder: "token  or  username:token",
    scopes: [],
    tokenHint:
      "Paste a bearer token, or 'username:token' for HTTP-basic auth. The base URL should be the repository clone URL (e.g. https://git.example.com/org/repo.git).",
  },
};

/**
 * Build a deep link to the provider's token-creation page with recommended
 * scopes and a sensible default name. Falls back to a sane public URL when
 * the user hasn't filled in a base URL yet.
 */
function buildTokenUrl(
  provider: GitProviderType,
  rawBaseUrl: string,
): string | null {
  if (provider === "generic") return null;
  const fallbackHost =
    provider === "github" ? "github.com" : "gitlab.com";
  let host = fallbackHost;
  let scheme = "https:";
  try {
    if (rawBaseUrl) {
      const u = new URL(rawBaseUrl);
      scheme = u.protocol;
      host = u.host || fallbackHost;
      // github.com's API host needs to be rewritten back to the UI host.
      if (host === "api.github.com") host = "github.com";
    }
  } catch {
    // Invalid URL — fall back to public defaults below.
  }
  const scopes = PROVIDER_META[provider].scopes.join(",");
  if (provider === "github") {
    return `${scheme}//${host}/settings/tokens/new?scopes=${encodeURIComponent(scopes)}&description=${encodeURIComponent("MegooCI")}`;
  }
  // GitLab (SaaS or self-hosted)
  return `${scheme}//${host}/-/user_settings/personal_access_tokens?name=${encodeURIComponent("MegooCI")}&scopes=${encodeURIComponent(scopes)}`;
}

function ValidationBadge({ connection }: { connection: GitConnection }) {
  if (connection.validation_status === "ok") {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" /> Validated
      </Badge>
    );
  }
  if (connection.validation_status === "failed") {
    return (
      <Badge variant="failed" className="gap-1">
        <XCircle className="h-3 w-3" /> Failed
      </Badge>
    );
  }
  return (
    <Badge variant="pending" className="gap-1">
      Not tested
    </Badge>
  );
}

function ProviderIcon({ type }: { type: string }) {
  const meta = PROVIDER_META[(type as GitProviderType) || "generic"] ||
    PROVIDER_META.generic;
  const Icon = meta.icon;
  return <Icon className="h-4 w-4" />;
}

// -----------------------------------------------------------------------------
// TokenPanel — visually promoted section for the PAT credential.
//
// It's the single most sensitive step in the dialog, so it's rendered as a
// bordered + tinted panel instead of a one-line input. Includes:
//   - a direct link to the provider's token-creation page,
//   - the required scopes as copyable code chips,
//   - a show/hide visibility toggle,
//   - when editing, a "current token" badge showing the bcrypt hint.
// -----------------------------------------------------------------------------
function TokenPanel({
  editing,
  existingHint,
  provider,
  baseUrl,
  value,
  onChange,
  showToken,
  onToggleShow,
}: {
  editing: boolean;
  existingHint: string | null;
  provider: GitProviderType;
  baseUrl: string;
  value: string;
  onChange: (v: string) => void;
  showToken: boolean;
  onToggleShow: () => void;
}) {
  const meta = PROVIDER_META[provider];
  const tokenUrl = buildTokenUrl(provider, baseUrl);
  const showExisting = editing && existingHint;

  return (
    <div className="space-y-3 rounded-lg border-2 border-primary/30 bg-primary/5 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10">
            <KeyRound className="h-4 w-4 text-primary" />
          </div>
          <div>
            <div className="text-sm font-semibold">
              {editing ? "Rotate token" : meta.tokenLabel}
            </div>
            <div className="text-xs text-muted-foreground">
              {editing
                ? "Paste a new token to rotate it, or leave empty to keep the current one."
                : "Encrypted at rest; shown to the server only during this request."}
            </div>
          </div>
        </div>
        {showExisting && (
          <Badge variant="secondary" className="font-mono text-xs">
            current: {"\u2022\u2022\u2022\u2022"}
            {existingHint}
          </Badge>
        )}
      </div>

      {/* Provider-specific quick link + required scopes. */}
      {provider !== "generic" && (
        <div className="rounded-md border bg-background p-3">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-xs font-medium">
              Don&apos;t have a token yet?
            </div>
            {tokenUrl && (
              <a
                href={tokenUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/5 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/10"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                Create one on {meta.label}
              </a>
            )}
          </div>
          {meta.scopes.length > 0 && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-muted-foreground">Required scopes:</span>
              {meta.scopes.map((scope) => (
                <code
                  key={scope}
                  className="rounded border bg-muted px-1.5 py-0.5 font-mono"
                >
                  {scope}
                </code>
              ))}
            </div>
          )}
        </div>
      )}

      {/* The actual input — big, monospace, with a visibility toggle. */}
      <div className="space-y-1.5">
        <label
          htmlFor="git-token-input"
          className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
        >
          {editing ? "New token (optional)" : "Paste your token"}
        </label>
        <div className="relative">
          <Input
            id="git-token-input"
            type={showToken ? "text" : "password"}
            placeholder={meta.tokenPlaceholder}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            spellCheck={false}
            autoComplete="off"
            className="h-11 pr-11 font-mono text-sm tracking-tight"
          />
          <button
            type="button"
            onClick={onToggleShow}
            title={showToken ? "Hide token" : "Show token"}
            aria-label={showToken ? "Hide token" : "Show token"}
            className="absolute right-1 top-1/2 inline-flex h-9 w-9 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            {showToken ? (
              <EyeOff className="h-4 w-4" />
            ) : (
              <Eye className="h-4 w-4" />
            )}
          </button>
        </div>
        <p className="text-xs text-muted-foreground">{meta.tokenHint}</p>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------
// Notification channel type metadata
// --------------------------------------------------------------------------
interface NotifChannelMeta {
  label: string;
  icon: React.ElementType;
  fields: { key: string; label: string; type: string; placeholder: string; required?: boolean }[];
}

const NOTIF_CHANNEL_META: Record<NotificationChannelType, NotifChannelMeta> = {
  email: {
    label: "Email (SMTP)",
    icon: Mail,
    fields: [
      { key: "smtp_host", label: "SMTP Host", type: "text", placeholder: "smtp.example.com", required: true },
      { key: "smtp_port", label: "SMTP Port", type: "number", placeholder: "587" },
      { key: "smtp_user", label: "SMTP Username", type: "text", placeholder: "user@example.com" },
      { key: "smtp_password", label: "SMTP Password", type: "password", placeholder: "password" },
      { key: "from_email", label: "From Email", type: "text", placeholder: "noreply@megooci.local", required: true },
      { key: "from_name", label: "From Name", type: "text", placeholder: "MegooCI" },
      { key: "tls", label: "Use TLS", type: "checkbox", placeholder: "" },
    ],
  },
  slack: {
    label: "Slack",
    icon: Hash,
    fields: [
      { key: "webhook_url", label: "Webhook URL", type: "password", placeholder: "https://hooks.slack.com/services/...", required: true },
    ],
  },
  telegram: {
    label: "Telegram",
    icon: Send,
    fields: [
      { key: "bot_token", label: "Bot Token", type: "password", placeholder: "123456:ABC-DEF...", required: true },
      { key: "default_chat_id", label: "Default Chat ID", type: "text", placeholder: "-1001234567890", required: true },
    ],
  },
};

function NotifChannelIcon({ type }: { type: string }) {
  const meta = NOTIF_CHANNEL_META[type as NotificationChannelType];
  if (!meta) return <Bell className="h-4 w-4" />;
  const Icon = meta.icon;
  return <Icon className="h-4 w-4" />;
}

function NotifValidationBadge({ channel }: { channel: NotificationChannel }) {
  if (channel.validation_status === "ok") {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" /> Validated
      </Badge>
    );
  }
  if (channel.validation_status === "failed") {
    return (
      <Badge variant="failed" className="gap-1">
        <XCircle className="h-3 w-3" /> Failed
      </Badge>
    );
  }
  return (
    <Badge variant="pending" className="gap-1">
      Not tested
    </Badge>
  );
}

export default function IntegrationsPage() {
  const { user } = useAuthStore();
  const confirm = useConfirm();
  const isAdmin = user?.is_admin ?? false;

  const [connections, setConnections] = React.useState<GitConnection[]>([]);
  const [loading, setLoading] = React.useState(true);

  // ---------- Notification channels state ----------
  const [notifChannels, setNotifChannels] = React.useState<NotificationChannel[]>([]);
  const [notifLoading, setNotifLoading] = React.useState(true);
  const [notifDialogOpen, setNotifDialogOpen] = React.useState(false);
  const [notifEditing, setNotifEditing] = React.useState<NotificationChannel | null>(null);
  const [notifForm, setNotifForm] = React.useState<{
    name: string;
    channel_type: NotificationChannelType;
    config: Record<string, unknown>;
  }>({ name: "", channel_type: "email", config: { smtp_port: 587, tls: true } });
  const [notifSubmitting, setNotifSubmitting] = React.useState(false);
  const [notifTestingId, setNotifTestingId] = React.useState<string | null>(null);
  const [notifShowSecrets, setNotifShowSecrets] = React.useState<Record<string, boolean>>({});

  // Create / edit dialog
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<GitConnection | null>(null);
  const [form, setForm] = React.useState({
    name: "",
    provider_type: "github" as GitProviderType,
    base_url: PROVIDER_META.github.defaultBaseUrl,
    credential: "",
  });
  const [submitting, setSubmitting] = React.useState(false);
  // Toggles plain-text visibility of the token input (Eye / EyeOff).
  const [showToken, setShowToken] = React.useState(false);

  // Per-row "testing" spinner state
  const [testingId, setTestingId] = React.useState<string | null>(null);

  async function loadConnections() {
    try {
      const data = await gitConnectionsApi.list();
      setConnections(data);
    } catch {
      toast.error("Failed to load Git connections");
    } finally {
      setLoading(false);
    }
  }

  async function loadNotifChannels() {
    try {
      const data = await notificationChannelsApi.list();
      setNotifChannels(data);
    } catch {
      toast.error("Failed to load notification channels");
    } finally {
      setNotifLoading(false);
    }
  }

  React.useEffect(() => {
    if (isAdmin) {
      loadConnections();
      loadNotifChannels();
    } else {
      setLoading(false);
      setNotifLoading(false);
    }
  }, [isAdmin]);

  function openNotifCreate() {
    setNotifEditing(null);
    setNotifShowSecrets({});
    setNotifForm({ name: "", channel_type: "email", config: { smtp_port: 587, tls: true } });
    setNotifDialogOpen(true);
  }

  function openNotifEdit(ch: NotificationChannel) {
    setNotifEditing(ch);
    setNotifShowSecrets({});
    const config: Record<string, unknown> = { ...(ch.config_summary || {}) };
    Object.keys(config).forEach((k) => {
      if (config[k] === "\u2022\u2022\u2022\u2022") config[k] = "";
    });
    setNotifForm({
      name: ch.name,
      channel_type: ch.channel_type as NotificationChannelType,
      config,
    });
    setNotifDialogOpen(true);
  }

  function onNotifTypeChange(type: NotificationChannelType) {
    const defaults: Record<string, Record<string, unknown>> = {
      email: { smtp_port: 587, tls: true },
      slack: {},
      telegram: {},
    };
    setNotifForm((prev) => ({
      ...prev,
      channel_type: type,
      config: defaults[type] || {},
    }));
  }

  async function handleNotifSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!notifForm.name.trim()) {
      toast.error("Name is required");
      return;
    }
    setNotifSubmitting(true);
    try {
      if (notifEditing) {
        const configToSend: Record<string, unknown> = {};
        Object.entries(notifForm.config).forEach(([k, v]) => {
          if (v !== "" && v !== undefined) configToSend[k] = v;
        });
        await notificationChannelsApi.update(notifEditing.id, {
          name: notifForm.name.trim(),
          config: Object.keys(configToSend).length > 0 ? configToSend : undefined,
        });
        toast.success("Channel updated");
      } else {
        await notificationChannelsApi.create({
          name: notifForm.name.trim(),
          channel_type: notifForm.channel_type,
          config: notifForm.config,
        });
        toast.success("Channel created");
      }
      setNotifDialogOpen(false);
      setNotifEditing(null);
      loadNotifChannels();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Request failed");
    } finally {
      setNotifSubmitting(false);
    }
  }

  async function handleNotifTest(ch: NotificationChannel) {
    setNotifTestingId(ch.id);
    try {
      const result: NotificationChannelTestResult = await notificationChannelsApi.test(ch.id);
      if (result.ok) toast.success(`Test passed: ${result.detail}`);
      else toast.error(`Test failed: ${result.detail}`);
      loadNotifChannels();
    } catch {
      toast.error("Test request failed");
    } finally {
      setNotifTestingId(null);
    }
  }

  async function handleNotifToggle(ch: NotificationChannel) {
    try {
      await notificationChannelsApi.update(ch.id, { enabled: !ch.enabled });
      toast.success(ch.enabled ? "Channel disabled" : "Channel enabled");
      loadNotifChannels();
    } catch {
      toast.error("Failed to toggle channel");
    }
  }

  async function handleNotifDelete(ch: NotificationChannel) {
    const ok = await confirm({
      title: `Delete channel '${ch.name}'?`,
      description: "This will permanently delete the notification channel and all its delivery history.",
      confirmText: "Delete channel",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await notificationChannelsApi.delete(ch.id);
      toast.success("Channel deleted");
      loadNotifChannels();
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Delete failed");
    }
  }

  function openCreate() {
    setEditing(null);
    setShowToken(false);
    setForm({
      name: "",
      provider_type: "github",
      base_url: PROVIDER_META.github.defaultBaseUrl,
      credential: "",
    });
    setDialogOpen(true);
  }

  function openEdit(c: GitConnection) {
    setEditing(c);
    setShowToken(false);
    setForm({
      name: c.name,
      provider_type: (c.provider_type as GitProviderType) || "generic",
      base_url: c.base_url || "",
      credential: "",
    });
    setDialogOpen(true);
  }

  function onProviderChange(provider: GitProviderType) {
    setForm((prev) => ({
      ...prev,
      provider_type: provider,
      base_url: PROVIDER_META[provider].defaultBaseUrl,
    }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.name.trim()) {
      toast.error("Name is required");
      return;
    }
    if (!editing && !form.credential.trim()) {
      toast.error("Token is required");
      return;
    }
    setSubmitting(true);
    try {
      let connection: GitConnection;
      if (editing) {
        connection = await gitConnectionsApi.update(editing.id, {
          name: form.name.trim(),
          base_url: form.base_url.trim() || null,
          credential: form.credential ? form.credential : undefined,
        });
      } else {
        connection = await gitConnectionsApi.create({
          name: form.name.trim(),
          provider_type: form.provider_type,
          base_url: form.base_url.trim() || null,
          auth_mode: "pat",
          credential: form.credential,
        });
      }
      toast.success(
        editing ? "Connection updated" : "Connection created",
      );
      if (!editing && connection.validation_status === "failed") {
        toast.warning(
          `Validation failed: ${connection.validation_error ?? "unknown"}`,
        );
      }
      setDialogOpen(false);
      setEditing(null);
      loadConnections();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Request failed";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleTest(c: GitConnection) {
    setTestingId(c.id);
    try {
      const result: GitConnectionTestResult = await gitConnectionsApi.test(
        c.id,
      );
      if (result.ok) toast.success(`Validated: ${result.detail}`);
      else toast.error(`Validation failed: ${result.detail}`);
      loadConnections();
    } catch {
      toast.error("Test request failed");
    } finally {
      setTestingId(null);
    }
  }

  async function handleDelete(c: GitConnection) {
    const ok = await confirm({
      title: `Delete connection '${c.name}'?`,
      description: (
        <>
          This action cannot be undone. Linked project repositories must be
          unlinked first or the delete will be refused.
        </>
      ),
      confirmText: "Delete connection",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await gitConnectionsApi.delete(c.id);
      toast.success("Connection deleted");
      loadConnections();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Delete failed";
      toast.error(message);
    }
  }

  if (!isAdmin) {
    return (
      <RequireAdmin
        fallback={
          <AppLayout>
            <div className="mx-auto max-w-3xl">
              <Card>
                <CardContent className="py-12 text-center text-sm text-muted-foreground">
                  Integrations are managed by administrators. Ask an admin to add
                  a Git provider connection.
                </CardContent>
              </Card>
            </div>
          </AppLayout>
        }
      >
        <div />
      </RequireAdmin>
    );
  }

  return (
    <AppLayout>
      <div className="mx-auto max-w-5xl space-y-6">
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            Integrations
          </h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            Manage notification channels and Git provider connections used
            across your pipelines and projects.
          </p>
        </div>

        {/* ============================================================
           Notification Channels
           ============================================================ */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Notification Channels</CardTitle>
            <Dialog open={notifDialogOpen} onOpenChange={setNotifDialogOpen}>
              <Button onClick={openNotifCreate} size="sm" variant="outline">
                <Plus className="mr-1.5 h-4 w-4" /> New channel
              </Button>
              <DialogContent className="max-w-xl">
                <DialogHeader>
                  <DialogTitle>
                    {notifEditing ? "Edit channel" : "New notification channel"}
                  </DialogTitle>
                  <DialogDescription>
                    {notifEditing
                      ? "Update channel settings. Leave secret fields empty to keep existing values."
                      : "Configure a notification channel for email, Slack, or Telegram."}
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleNotifSubmit} className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Name</label>
                    <Input
                      placeholder="deploy-alerts"
                      value={notifForm.name}
                      onChange={(e) =>
                        setNotifForm((p) => ({ ...p, name: e.target.value }))
                      }
                      autoFocus
                    />
                    <p className="text-xs text-muted-foreground">
                      Use this name in pipeline YAML: <code className="rounded bg-muted px-1">notify: channel: &quot;{notifForm.name || "name"}&quot;</code>
                    </p>
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium">Type</label>
                    {notifEditing ? (
                      <Input
                        value={NOTIF_CHANNEL_META[notifForm.channel_type]?.label ?? notifForm.channel_type}
                        readOnly
                        className="bg-muted"
                      />
                    ) : (
                      <Select
                        value={notifForm.channel_type}
                        onChange={(e) => onNotifTypeChange(e.target.value as NotificationChannelType)}
                        options={[
                          { value: "email", label: "Email (SMTP)" },
                          { value: "slack", label: "Slack" },
                          { value: "telegram", label: "Telegram" },
                        ]}
                      />
                    )}
                  </div>

                  <div className="space-y-3 rounded-lg border-2 border-primary/30 bg-primary/5 p-4">
                    <div className="flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10">
                        <NotifChannelIcon type={notifForm.channel_type} />
                      </div>
                      <div className="text-sm font-semibold">
                        {NOTIF_CHANNEL_META[notifForm.channel_type]?.label} Configuration
                      </div>
                    </div>

                    {NOTIF_CHANNEL_META[notifForm.channel_type]?.fields.map((field) => (
                      <div key={field.key} className="space-y-1">
                        <label className="text-xs font-medium">
                          {field.label}
                          {field.required && <span className="text-destructive ml-0.5">*</span>}
                        </label>
                        {field.type === "checkbox" ? (
                          <div className="flex items-center gap-2">
                            <input
                              type="checkbox"
                              checked={!!notifForm.config[field.key]}
                              onChange={(e) =>
                                setNotifForm((p) => ({
                                  ...p,
                                  config: { ...p.config, [field.key]: e.target.checked },
                                }))
                              }
                              className="h-4 w-4 rounded border"
                            />
                            <span className="text-xs text-muted-foreground">Enabled</span>
                          </div>
                        ) : field.type === "password" ? (
                          <div className="relative">
                            <Input
                              type={notifShowSecrets[field.key] ? "text" : "password"}
                              placeholder={
                                notifEditing
                                  ? `Leave empty to keep current`
                                  : field.placeholder
                              }
                              value={(notifForm.config[field.key] as string) ?? ""}
                              onChange={(e) =>
                                setNotifForm((p) => ({
                                  ...p,
                                  config: { ...p.config, [field.key]: e.target.value },
                                }))
                              }
                              spellCheck={false}
                              autoComplete="off"
                              className="pr-11 font-mono text-sm"
                            />
                            <button
                              type="button"
                              onClick={() =>
                                setNotifShowSecrets((p) => ({
                                  ...p,
                                  [field.key]: !p[field.key],
                                }))
                              }
                              className="absolute right-1 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground"
                            >
                              {notifShowSecrets[field.key] ? (
                                <EyeOff className="h-4 w-4" />
                              ) : (
                                <Eye className="h-4 w-4" />
                              )}
                            </button>
                          </div>
                        ) : (
                          <Input
                            type={field.type}
                            placeholder={field.placeholder}
                            value={(notifForm.config[field.key] as string) ?? ""}
                            onChange={(e) =>
                              setNotifForm((p) => ({
                                ...p,
                                config: {
                                  ...p.config,
                                  [field.key]: field.type === "number" ? Number(e.target.value) : e.target.value,
                                },
                              }))
                            }
                          />
                        )}
                      </div>
                    ))}
                  </div>

                  <DialogFooter>
                    <Button type="button" variant="outline" onClick={() => setNotifDialogOpen(false)}>
                      Cancel
                    </Button>
                    <Button type="submit" disabled={notifSubmitting}>
                      {notifSubmitting ? "Saving\u2026" : notifEditing ? "Save changes" : "Create channel"}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          </CardHeader>
          <CardContent>
            {notifLoading ? (
              <div className="space-y-3">
                {Array.from({ length: 2 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : notifChannels.length === 0 ? (
              <div className="py-12 text-center">
                <Bell className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">
                  No notification channels configured. Add one to send
                  notifications from your pipelines.
                </p>
              </div>
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="w-full min-w-[540px] text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-3 pr-4 font-medium">Name</th>
                      <th className="pb-3 pr-4 font-medium">Type</th>
                      <th className="pb-3 pr-4 font-medium">Status</th>
                      <th className="pb-3 pr-4 font-medium">Enabled</th>
                      <th className="hidden pb-3 pr-4 font-medium lg:table-cell">Validated</th>
                      <th className="pb-3 text-right font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {notifChannels.map((ch) => (
                      <tr key={ch.id} className="border-b last:border-0">
                        <td className="py-3 pr-4 font-medium">{ch.name}</td>
                        <td className="py-3 pr-4">
                          <span className="inline-flex items-center gap-1.5 capitalize">
                            <NotifChannelIcon type={ch.channel_type} />
                            {NOTIF_CHANNEL_META[ch.channel_type as NotificationChannelType]?.label ?? ch.channel_type}
                          </span>
                        </td>
                        <td className="py-3 pr-4">
                          <NotifValidationBadge channel={ch} />
                        </td>
                        <td className="py-3 pr-4">
                          <button
                            onClick={() => handleNotifToggle(ch)}
                            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium transition-colors ${
                              ch.enabled
                                ? "bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400"
                                : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-500"
                            }`}
                          >
                            {ch.enabled ? (
                              <><Power className="h-3 w-3" /> On</>
                            ) : (
                              <><PowerOff className="h-3 w-3" /> Off</>
                            )}
                          </button>
                        </td>
                        <td className="hidden py-3 pr-4 text-xs text-muted-foreground lg:table-cell">
                          {ch.last_validated_at
                            ? formatDistanceToNow(new Date(ch.last_validated_at), { addSuffix: true })
                            : "\u2014"}
                        </td>
                        <td className="py-3">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => handleNotifTest(ch)}
                              disabled={notifTestingId === ch.id}
                              title="Send test notification"
                            >
                              <RefreshCw className={`h-4 w-4 ${notifTestingId === ch.id ? "animate-spin" : ""}`} />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => openNotifEdit(ch)}
                              title="Edit"
                            >
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive"
                              onClick={() => handleNotifDelete(ch)}
                              title="Delete"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0">
            <CardTitle className="text-base">Git Connections</CardTitle>
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <Button onClick={openCreate} size="sm" variant="outline">
                <Plus className="mr-1.5 h-4 w-4" /> New connection
              </Button>
              <DialogContent className="max-w-xl">
                <DialogHeader>
                  <DialogTitle>
                    {editing ? "Edit connection" : "New Git connection"}
                  </DialogTitle>
                  <DialogDescription>
                    {editing
                      ? "Update connection details. Leave the token field empty to keep the existing token."
                      : "Register a Personal Access Token for a Git provider. Tokens are encrypted at rest and never returned from the API."}
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleSubmit} className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Name</label>
                    <Input
                      placeholder="Acme GitHub Org"
                      value={form.name}
                      onChange={(e) =>
                        setForm((p) => ({ ...p, name: e.target.value }))
                      }
                      autoFocus
                    />
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium">Provider</label>
                    {editing ? (
                      <Input
                        value={
                          PROVIDER_META[form.provider_type]?.label ??
                          form.provider_type
                        }
                        readOnly
                        className="bg-muted"
                      />
                    ) : (
                      <Select
                        value={form.provider_type}
                        onChange={(e) =>
                          onProviderChange(
                            e.target.value as GitProviderType,
                          )
                        }
                        options={[
                          { value: "github", label: "GitHub" },
                          { value: "gitlab", label: "GitLab" },
                          { value: "generic", label: "Generic Git" },
                        ]}
                      />
                    )}
                  </div>

                  <div className="space-y-2">
                    <label className="text-sm font-medium">
                      {form.provider_type === "generic"
                        ? "Repository URL (base)"
                        : "API base URL"}
                    </label>
                    <Input
                      placeholder={
                        form.provider_type === "generic"
                          ? "https://git.example.com/org/repo.git"
                          : PROVIDER_META[form.provider_type].defaultBaseUrl
                      }
                      value={form.base_url}
                      onChange={(e) =>
                        setForm((p) => ({ ...p, base_url: e.target.value }))
                      }
                    />
                  </div>

                  <TokenPanel
                    editing={!!editing}
                    existingHint={editing?.credential_hint ?? null}
                    provider={form.provider_type}
                    baseUrl={form.base_url}
                    value={form.credential}
                    onChange={(v) =>
                      setForm((p) => ({ ...p, credential: v }))
                    }
                    showToken={showToken}
                    onToggleShow={() => setShowToken((v) => !v)}
                  />

                  <DialogFooter>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setDialogOpen(false)}
                    >
                      Cancel
                    </Button>
                    <Button type="submit" disabled={submitting}>
                      {submitting
                        ? "Saving\u2026"
                        : editing
                          ? "Save changes"
                          : "Create & test"}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : connections.length === 0 ? (
              <div className="py-12 text-center">
                <GitBranch className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">
                  No Git connections yet. Add one to let projects pull source
                  and subscribe to webhooks.
                </p>
              </div>
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="w-full min-w-[640px] text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-3 pr-4 font-medium">Name</th>
                      <th className="pb-3 pr-4 font-medium">Provider</th>
                      <th className="hidden pb-3 pr-4 font-medium md:table-cell">
                        Base URL
                      </th>
                      <th className="pb-3 pr-4 font-medium">Token</th>
                      <th className="pb-3 pr-4 font-medium">Status</th>
                      <th className="hidden pb-3 pr-4 font-medium lg:table-cell">
                        Validated
                      </th>
                      <th className="pb-3 text-right font-medium">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {connections.map((c) => (
                      <tr key={c.id} className="border-b last:border-0">
                        <td className="py-3 pr-4 font-medium">{c.name}</td>
                        <td className="py-3 pr-4">
                          <span className="inline-flex items-center gap-1.5 capitalize">
                            <ProviderIcon type={c.provider_type} />
                            {c.provider_type}
                          </span>
                        </td>
                        <td className="hidden max-w-[200px] truncate py-3 pr-4 text-muted-foreground md:table-cell">
                          {c.base_url || "\u2014"}
                        </td>
                        <td className="py-3 pr-4 font-mono text-xs text-muted-foreground">
                          {c.credential_hint
                            ? `\u2022\u2022\u2022\u2022${c.credential_hint}`
                            : "\u2014"}
                        </td>
                        <td className="py-3 pr-4">
                          <ValidationBadge connection={c} />
                        </td>
                        <td className="hidden py-3 pr-4 text-xs text-muted-foreground lg:table-cell">
                          {c.last_validated_at
                            ? formatDistanceToNow(
                                new Date(c.last_validated_at),
                                { addSuffix: true },
                              )
                            : "\u2014"}
                        </td>
                        <td className="py-3">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => handleTest(c)}
                              disabled={testingId === c.id}
                              title="Test connection"
                            >
                              <RefreshCw
                                className={`h-4 w-4 ${
                                  testingId === c.id ? "animate-spin" : ""
                                }`}
                              />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => openEdit(c)}
                              title="Edit"
                            >
                              <Pencil className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive"
                              onClick={() => handleDelete(c)}
                              title="Delete"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
