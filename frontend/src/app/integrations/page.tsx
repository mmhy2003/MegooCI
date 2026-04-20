"use client";

import * as React from "react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  CheckCircle2,
  Github,
  GitBranch,
  GitlabIcon,
  Globe,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  XCircle,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import {
  gitConnectionsApi,
  type GitConnection,
  type GitConnectionTestResult,
  type GitProviderType,
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

const PROVIDER_META: Record<
  GitProviderType,
  {
    label: string;
    defaultBaseUrl: string;
    icon: React.ElementType;
    tokenHint: string;
  }
> = {
  github: {
    label: "GitHub",
    defaultBaseUrl: "https://api.github.com",
    icon: Github,
    tokenHint:
      "Create a Personal Access Token (classic or fine-grained) with repo scope at GitHub \u2192 Settings \u2192 Developer settings \u2192 Personal access tokens.",
  },
  gitlab: {
    label: "GitLab",
    defaultBaseUrl: "https://gitlab.com",
    icon: GitlabIcon,
    tokenHint:
      "Create a Personal Access Token with api and read_repository scopes at GitLab \u2192 Preferences \u2192 Access Tokens.",
  },
  generic: {
    label: "Generic Git",
    defaultBaseUrl: "",
    icon: Globe,
    tokenHint:
      "Use a token, or 'username:token' for HTTP-basic. Base URL should be the repository clone URL.",
  },
};

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

export default function IntegrationsPage() {
  const { user } = useAuthStore();
  const confirm = useConfirm();
  const isAdmin = user?.is_admin ?? false;

  const [connections, setConnections] = React.useState<GitConnection[]>([]);
  const [loading, setLoading] = React.useState(true);

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

  React.useEffect(() => {
    if (isAdmin) loadConnections();
    else setLoading(false);
  }, [isAdmin]);

  function openCreate() {
    setEditing(null);
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
    );
  }

  return (
    <AppLayout>
      <div className="mx-auto max-w-5xl space-y-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
              Integrations
            </h1>
            <p className="text-sm text-muted-foreground sm:text-base">
              Connect MegooCI to GitHub, GitLab, or any Git host using a
              Personal Access Token. Projects reuse these connections to link
              specific repositories.
            </p>
          </div>
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <Button onClick={openCreate} className="w-full sm:w-auto">
              <Plus className="mr-1.5 h-4 w-4" /> New connection
            </Button>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>
                  {editing ? "Edit connection" : "New Git connection"}
                </DialogTitle>
                <DialogDescription>
                  {editing
                    ? "Update connection details. Leave token empty to keep the existing token."
                    : "Register a Personal Access Token for a Git provider. Tokens are encrypted at rest."}
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
                      value={PROVIDER_META[
                        form.provider_type
                      ]?.label ?? form.provider_type}
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

                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    {editing ? "New token (leave empty to keep current)" : "Token"}
                  </label>
                  <Input
                    type="password"
                    placeholder="ghp_...  /  glpat-...  /  user:token"
                    value={form.credential}
                    onChange={(e) =>
                      setForm((p) => ({ ...p, credential: e.target.value }))
                    }
                  />
                  <p className="text-xs text-muted-foreground">
                    {PROVIDER_META[form.provider_type].tokenHint}
                  </p>
                </div>

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
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Git connections</CardTitle>
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
