"use client";

import * as React from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clipboard,
  ClipboardCheck,
  ExternalLink,
  GitBranch,
  Globe,
  KeyRound,
  Link2,
  Lock,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  gitConnectionsApi,
  projectRepositoriesApi,
  type GitConnection,
  type ProjectRepository,
  type ProjectRepositoryWithSecret,
  type ProviderRepositoryInfo,
  type WebhookDelivery,
} from "@/lib/api";
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

function shorten(sha: string | null | undefined, n = 7): string {
  if (!sha) return "\u2014";
  return sha.slice(0, n);
}

function providerLabel(type: string): string {
  if (type === "github") return "GitHub";
  if (type === "gitlab") return "GitLab";
  if (type === "generic") return "Generic Git";
  return type;
}

function statusTone(
  statusLabel: string | null,
): "success" | "failed" | "pending" | "cancelled" {
  if (statusLabel === "accepted") return "success";
  if (statusLabel === "rejected") return "failed";
  if (statusLabel === "duplicate") return "cancelled";
  return "pending";
}

// Copy-to-clipboard helper button used across the webhook drawer.
function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = React.useState(false);
  async function handle() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error("Clipboard access denied");
    }
  }
  return (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={handle}
      className="shrink-0"
    >
      {copied ? (
        <ClipboardCheck className="mr-1.5 h-3.5 w-3.5" />
      ) : (
        <Clipboard className="mr-1.5 h-3.5 w-3.5" />
      )}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

// Provider-specific manual webhook setup instructions (F-16.6).
function WebhookInstructions({ providerType }: { providerType: string }) {
  if (providerType === "github") {
    return (
      <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
        <li>
          Open the repository on GitHub and navigate to{" "}
          <span className="font-medium text-foreground">
            Settings &rarr; Webhooks &rarr; Add webhook
          </span>
          .
        </li>
        <li>
          Paste the <span className="font-medium text-foreground">Webhook URL</span>{" "}
          below into the <em>Payload URL</em> field.
        </li>
        <li>
          Set content type to{" "}
          <code className="rounded bg-muted px-1 py-0.5 text-xs">
            application/json
          </code>
          .
        </li>
        <li>
          Paste the <span className="font-medium text-foreground">Secret</span>{" "}
          into the <em>Secret</em> field.
        </li>
        <li>
          Under <em>Which events would you like to trigger this webhook?</em>
          , select <em>Just the push event</em> (or add{" "}
          <em>Pull requests</em> if you want those recorded).
        </li>
        <li>Click <em>Add webhook</em> and confirm the ping test succeeds.</li>
      </ol>
    );
  }
  if (providerType === "gitlab") {
    return (
      <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
        <li>
          Open the project on GitLab and go to{" "}
          <span className="font-medium text-foreground">
            Settings &rarr; Webhooks
          </span>
          .
        </li>
        <li>
          Paste the <span className="font-medium text-foreground">Webhook URL</span>{" "}
          into the <em>URL</em> field.
        </li>
        <li>
          Paste the <span className="font-medium text-foreground">Secret</span>{" "}
          into the <em>Secret token</em> field.
        </li>
        <li>
          Check <em>Push events</em> and <em>Tag push events</em>; leave SSL
          verification on.
        </li>
        <li>
          Click <em>Add webhook</em> and use the <em>Test</em> dropdown to
          send a push event.
        </li>
      </ol>
    );
  }
  return (
    <div className="space-y-2 text-sm text-muted-foreground">
      <p>
        Send a <code className="rounded bg-muted px-1 py-0.5">POST</code>{" "}
        request with a JSON body. Include the{" "}
        <code className="rounded bg-muted px-1 py-0.5">
          X-MegooCI-Signature
        </code>{" "}
        header as{" "}
        <code className="rounded bg-muted px-1 py-0.5">
          sha256=&lt;hex&gt;
        </code>{" "}
        computed as HMAC-SHA256 of the raw body using the secret below.
      </p>
      <p>Example (bash):</p>
      <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
        <code>{`BODY='{"ref":"refs/heads/main","after":"<sha>","pusher":{"name":"user"}}'
SIG=$(echo -n "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')

curl -X POST "$WEBHOOK_URL" \\
  -H "Content-Type: application/json" \\
  -H "X-MegooCI-Signature: sha256=$SIG" \\
  -H "X-MegooCI-Event: push" \\
  -H "X-MegooCI-Delivery: $(uuidgen)" \\
  --data "$BODY"`}</code>
      </pre>
    </div>
  );
}

// ----------------------------------------------------------------------------
// Webhook setup drawer
// ----------------------------------------------------------------------------
function WebhookSetupDialog({
  open,
  onOpenChange,
  projectId,
  repo,
  providerType,
  initialSecret,
  initialUrl,
  onRotated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  projectId: string;
  repo: ProjectRepository | null;
  providerType: string;
  initialSecret: string | null;
  initialUrl: string | null;
  onRotated: (updated: ProjectRepositoryWithSecret) => void;
}) {
  const confirm = useConfirm();
  const [rotating, setRotating] = React.useState(false);
  const [secret, setSecret] = React.useState<string | null>(initialSecret);
  const [url, setUrl] = React.useState<string | null>(initialUrl);

  // When a new secret arrives (create / rotate), refresh local state.
  React.useEffect(() => {
    setSecret(initialSecret);
    setUrl(initialUrl);
  }, [initialSecret, initialUrl, repo?.id]);

  if (!repo) return null;

  async function handleRotate() {
    if (!repo) return;
    const ok = await confirm({
      title: "Rotate webhook secret?",
      description: (
        <>
          The current secret will be invalidated immediately. Any provider
          still using the old secret will fail signature verification until
          you update it.
        </>
      ),
      confirmText: "Rotate secret",
      cancelText: "Keep current",
      tone: "warning",
    });
    if (!ok) return;
    setRotating(true);
    try {
      const updated = await projectRepositoriesApi.rotateSecret(
        projectId,
        repo.id,
      );
      setSecret(updated.webhook_secret);
      setUrl(updated.webhook_url);
      onRotated(updated);
      toast.success("Webhook secret rotated");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Rotate failed";
      toast.error(message);
    } finally {
      setRotating(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Webhook setup</DialogTitle>
          <DialogDescription>
            Paste the URL and secret below into the{" "}
            <span className="font-medium">{providerLabel(providerType)}</span>{" "}
            webhook settings for{" "}
            <code className="break-all">{repo.repo_url}</code>.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Webhook URL</label>
            <div className="flex items-start gap-2">
              <code className="flex-1 overflow-x-auto break-all rounded-md bg-muted px-3 py-2 font-mono text-xs">
                {url ?? "\u2014"}
              </code>
              {url && <CopyButton value={url} />}
            </div>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">Secret</label>
            {secret ? (
              <div className="flex items-start gap-2">
                <code className="flex-1 overflow-x-auto break-all rounded-md bg-muted px-3 py-2 font-mono text-xs">
                  {secret}
                </code>
                <CopyButton value={secret} />
              </div>
            ) : (
              <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-xs text-amber-700 dark:text-amber-400">
                <AlertTriangle className="h-4 w-4" />
                The current secret is not recoverable. Rotate it below to
                generate a new one (this will invalidate the old secret).
              </div>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={rotating}
              onClick={handleRotate}
            >
              <RefreshCw
                className={`mr-1.5 h-3.5 w-3.5 ${rotating ? "animate-spin" : ""}`}
              />
              {rotating ? "Rotating\u2026" : "Rotate secret"}
            </Button>
          </div>

          <div className="space-y-2">
            <label className="text-sm font-medium">
              Instructions for {providerLabel(providerType)}
            </label>
            <div className="rounded-md border p-3">
              <WebhookInstructions providerType={providerType} />
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ----------------------------------------------------------------------------
// Deliveries drawer
// ----------------------------------------------------------------------------
function DeliveriesDialog({
  open,
  onOpenChange,
  projectId,
  repo,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  projectId: string;
  repo: ProjectRepository | null;
}) {
  const [loading, setLoading] = React.useState(false);
  const [deliveries, setDeliveries] = React.useState<WebhookDelivery[]>([]);

  React.useEffect(() => {
    if (!open || !repo) return;
    let cancelled = false;
    setLoading(true);
    projectRepositoriesApi
      .deliveries(projectId, repo.id, 50)
      .then((rows) => {
        if (!cancelled) setDeliveries(rows);
      })
      .catch(() => {
        if (!cancelled) toast.error("Failed to load deliveries");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, projectId, repo]);

  if (!repo) return null;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>Webhook deliveries</DialogTitle>
          <DialogDescription>
            Last 50 inbound requests for{" "}
            <code className="break-all">{repo.repo_url}</code>. Rejected
            deliveries are shown in red.
          </DialogDescription>
        </DialogHeader>

        {loading ? (
          <div className="space-y-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : deliveries.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            No webhook deliveries recorded yet. Once your provider is
            configured, requests will show up here.
          </div>
        ) : (
          <div className="-mx-2 max-h-[60vh] overflow-y-auto px-2">
            <table className="w-full min-w-[560px] text-sm">
              <thead className="sticky top-0 bg-background">
                <tr className="border-b text-left text-muted-foreground">
                  <th className="pb-2 pr-3 font-medium">When</th>
                  <th className="pb-2 pr-3 font-medium">Event</th>
                  <th className="hidden pb-2 pr-3 font-medium sm:table-cell">
                    Branch
                  </th>
                  <th className="hidden pb-2 pr-3 font-medium md:table-cell">
                    Commit
                  </th>
                  <th className="pb-2 pr-3 font-medium">HTTP</th>
                  <th className="pb-2 font-medium">Sig</th>
                </tr>
              </thead>
              <tbody>
                {deliveries.map((d) => (
                  <tr key={d.id} className="border-b last:border-0 align-top">
                    <td className="py-2 pr-3 text-xs text-muted-foreground">
                      {formatDistanceToNow(new Date(d.received_at), {
                        addSuffix: true,
                      })}
                    </td>
                    <td className="py-2 pr-3 font-medium">
                      {d.event_type || "\u2014"}
                      {d.error && (
                        <div className="mt-0.5 text-xs text-red-600 dark:text-red-400">
                          {d.error}
                        </div>
                      )}
                    </td>
                    <td className="hidden py-2 pr-3 sm:table-cell">
                      {d.branch ? (
                        <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                          {d.branch}
                        </code>
                      ) : (
                        <span className="text-muted-foreground">{"\u2014"}</span>
                      )}
                    </td>
                    <td className="hidden py-2 pr-3 font-mono text-xs md:table-cell">
                      {shorten(d.commit_sha)}
                    </td>
                    <td className="py-2 pr-3">
                      <Badge
                        variant={d.http_status < 400 ? "success" : "failed"}
                      >
                        {d.http_status}
                      </Badge>
                    </td>
                    <td className="py-2">
                      {d.signature_valid ? (
                        <CheckCircle2
                          className="h-4 w-4 text-emerald-500"
                          aria-label="valid"
                        />
                      ) : (
                        <XCircle
                          className="h-4 w-4 text-red-500"
                          aria-label="invalid"
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ----------------------------------------------------------------------------
// Root component
// ----------------------------------------------------------------------------
export function ProjectIntegrations({ projectId }: { projectId: string }) {
  const confirm = useConfirm();
  const [loading, setLoading] = React.useState(true);
  const [repositories, setRepositories] = React.useState<ProjectRepository[]>(
    [],
  );
  const [connections, setConnections] = React.useState<GitConnection[]>([]);
  const [connectionsError, setConnectionsError] = React.useState(false);

  // Link dialog state
  const [linkOpen, setLinkOpen] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [linkForm, setLinkForm] = React.useState({
    connection_id: "",
    repo_url: "",
    default_branch: "main",
    display_name: "",
  });

  // Browse-repositories picker state. When the selected connection supports
  // listing (github / gitlab), we fetch repos the PAT can see so the user
  // picks from a list instead of typing a URL by hand.
  const [providerRepos, setProviderRepos] = React.useState<
    ProviderRepositoryInfo[]
  >([]);
  const [providerReposLoading, setProviderReposLoading] = React.useState(false);
  const [providerReposError, setProviderReposError] = React.useState<
    string | null
  >(null);
  const [providerSearch, setProviderSearch] = React.useState("");
  const [pickedRepoFullName, setPickedRepoFullName] = React.useState<
    string | null
  >(null);
  const [manualMode, setManualMode] = React.useState(false);

  // Webhook setup drawer state
  const [webhookRepo, setWebhookRepo] = React.useState<ProjectRepository | null>(
    null,
  );
  const [webhookSecret, setWebhookSecret] = React.useState<string | null>(null);
  const [webhookUrl, setWebhookUrl] = React.useState<string | null>(null);

  // Deliveries drawer state
  const [deliveriesRepo, setDeliveriesRepo] =
    React.useState<ProjectRepository | null>(null);

  async function loadRepositories() {
    try {
      const data = await projectRepositoriesApi.list(projectId);
      setRepositories(data);
    } catch {
      toast.error("Failed to load linked repositories");
    }
  }

  async function loadConnections() {
    try {
      const data = await gitConnectionsApi.list();
      setConnections(data);
      setConnectionsError(false);
    } catch {
      // Non-admin users hit 403 here; that's expected.
      setConnectionsError(true);
    }
  }

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      await Promise.all([loadRepositories(), loadConnections()]);
      if (!cancelled) setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  function connectionFor(id: string): GitConnection | undefined {
    return connections.find((c) => c.id === id);
  }

  function openLinkDialog() {
    setLinkForm({
      connection_id: connections[0]?.id || "",
      repo_url: "",
      default_branch: "main",
      display_name: "",
    });
    setPickedRepoFullName(null);
    setProviderSearch("");
    setManualMode(false);
    setProviderReposError(null);
    setProviderRepos([]);
    setLinkOpen(true);
  }

  // Fetch the repository list from the selected connection whenever the
  // dialog is open and the connection changes. Generic connections have no
  // list API, so we flip to manual mode automatically.
  React.useEffect(() => {
    if (!linkOpen) return;
    const conn = connections.find((c) => c.id === linkForm.connection_id);
    if (!conn) {
      setProviderRepos([]);
      setProviderReposError(null);
      return;
    }
    if (conn.provider_type === "generic") {
      setManualMode(true);
      setProviderRepos([]);
      setProviderReposError(null);
      return;
    }

    let cancelled = false;
    setProviderReposLoading(true);
    setProviderReposError(null);
    setProviderRepos([]);
    setPickedRepoFullName(null);
    setManualMode(false);

    gitConnectionsApi
      .repositories(conn.id, 100)
      .then((res) => {
        if (cancelled) return;
        if (!res.ok) {
          setProviderReposError(res.detail);
          setProviderRepos([]);
        } else {
          setProviderRepos(res.repositories);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg =
          err instanceof Error
            ? err.message
            : "Failed to list repositories";
        setProviderReposError(msg);
      })
      .finally(() => {
        if (!cancelled) setProviderReposLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [linkOpen, linkForm.connection_id, connections]);

  function pickProviderRepo(repo: ProviderRepositoryInfo) {
    setPickedRepoFullName(repo.full_name);
    setLinkForm((p) => ({
      ...p,
      repo_url: repo.clone_url,
      default_branch: repo.default_branch || "main",
      // Prefer the short "owner/name" as the display name when we have it,
      // but only overwrite if the user hasn't typed anything custom.
      display_name: p.display_name || repo.full_name,
    }));
  }

  async function handleLink(e: React.FormEvent) {
    e.preventDefault();
    if (!linkForm.connection_id) {
      toast.error("Pick a connection");
      return;
    }
    if (!linkForm.repo_url.trim()) {
      toast.error("Repository URL is required");
      return;
    }
    setSubmitting(true);
    try {
      const repo = await projectRepositoriesApi.create(projectId, {
        connection_id: linkForm.connection_id,
        repo_url: linkForm.repo_url.trim(),
        default_branch: linkForm.default_branch || "main",
        display_name: linkForm.display_name || null,
      });
      setLinkOpen(false);
      toast.success("Repository linked");
      // Immediately open the webhook setup drawer with the one-time secret.
      setWebhookRepo(repo);
      setWebhookSecret(repo.webhook_secret);
      setWebhookUrl(repo.webhook_url);
      loadRepositories();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Link failed";
      toast.error(message);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleUnlink(repo: ProjectRepository) {
    const ok = await confirm({
      title: "Unlink this repository?",
      description: (
        <>
          The webhook will stop accepting deliveries and its history will be
          removed. Pipelines still referencing this repository will fall back
          to their legacy <code>source_repo_url</code>, if any.
        </>
      ),
      confirmText: "Unlink repository",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await projectRepositoriesApi.delete(projectId, repo.id);
      toast.success("Repository unlinked");
      loadRepositories();
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Unlink failed";
      toast.error(message);
    }
  }

  function openWebhook(repo: ProjectRepository) {
    setWebhookRepo(repo);
    // We don't have the secret after reload; user must rotate to get a new one.
    setWebhookSecret(null);
    // Build the URL client-side from the slug so the drawer works offline of
    // public_url config changes.
    const origin =
      typeof window !== "undefined" ? window.location.origin : "";
    setWebhookUrl(`${origin}/api/v1/webhooks/git/${repo.webhook_slug}`);
  }

  const hasConnections = connections.length > 0;

  return (
    <div className="space-y-6">
      {/* Action bar */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <GitBranch className="h-4 w-4" />
              Linked repositories
            </CardTitle>
            <p className="mt-1 text-sm text-muted-foreground">
              Pushes to a linked repo trigger every matching pipeline in this
              project.
            </p>
          </div>
          <Dialog open={linkOpen} onOpenChange={setLinkOpen}>
            <Button
              size="sm"
              onClick={openLinkDialog}
              disabled={!hasConnections}
              title={
                hasConnections
                  ? undefined
                  : "Ask an admin to add a Git connection first"
              }
            >
              <Plus className="mr-1.5 h-4 w-4" /> Link repository
            </Button>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>Link a repository</DialogTitle>
                <DialogDescription>
                  Pick a Git connection and choose a repository. You&apos;ll
                  get a webhook URL + secret to paste into the provider.
                </DialogDescription>
              </DialogHeader>
              <form onSubmit={handleLink} className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Connection</label>
                  <Select
                    value={linkForm.connection_id}
                    onChange={(e) =>
                      setLinkForm((p) => ({
                        ...p,
                        connection_id: e.target.value,
                      }))
                    }
                    options={connections.map((c) => ({
                      value: c.id,
                      label: `${c.name} (${providerLabel(c.provider_type)})`,
                    }))}
                    placeholder="Pick a connection"
                  />
                </div>

                {/* Repository picker — list available repos when supported,
                    otherwise fall back to free-form URL input. */}
                {!manualMode ? (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <label className="text-sm font-medium">
                        Repository
                      </label>
                      <button
                        type="button"
                        onClick={() => {
                          setManualMode(true);
                          setPickedRepoFullName(null);
                        }}
                        className="text-xs text-muted-foreground underline hover:text-foreground"
                      >
                        Can&apos;t find it? Paste URL manually
                      </button>
                    </div>
                    <div className="relative">
                      <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        placeholder="Search repositories\u2026"
                        value={providerSearch}
                        onChange={(e) => setProviderSearch(e.target.value)}
                        className="pl-9"
                        disabled={
                          !linkForm.connection_id ||
                          providerReposLoading ||
                          providerRepos.length === 0
                        }
                      />
                    </div>
                    <div className="max-h-72 overflow-y-auto rounded-md border">
                      {providerReposLoading ? (
                        <div className="space-y-1 p-2">
                          {Array.from({ length: 5 }).map((_, i) => (
                            <Skeleton key={i} className="h-10 w-full" />
                          ))}
                        </div>
                      ) : providerReposError ? (
                        <div className="p-4 text-sm">
                          <div className="flex items-start gap-2 text-amber-700 dark:text-amber-400">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                            <div>
                              <div className="font-medium">
                                Couldn&apos;t list repositories
                              </div>
                              <div className="text-xs">
                                {providerReposError}
                              </div>
                              <button
                                type="button"
                                onClick={() => setManualMode(true)}
                                className="mt-1 text-xs underline"
                              >
                                Paste URL manually instead
                              </button>
                            </div>
                          </div>
                        </div>
                      ) : providerRepos.length === 0 ? (
                        <div className="p-6 text-center text-sm text-muted-foreground">
                          Select a connection to browse repositories.
                        </div>
                      ) : (
                        (() => {
                          const q = providerSearch.trim().toLowerCase();
                          const filtered = q
                            ? providerRepos.filter(
                                (r) =>
                                  r.full_name.toLowerCase().includes(q) ||
                                  (r.description || "")
                                    .toLowerCase()
                                    .includes(q),
                              )
                            : providerRepos;
                          if (filtered.length === 0) {
                            return (
                              <div className="p-4 text-center text-sm text-muted-foreground">
                                No repositories match &ldquo;{providerSearch}
                                &rdquo;.
                              </div>
                            );
                          }
                          return (
                            <ul className="divide-y">
                              {filtered.map((r) => {
                                const isSelected =
                                  pickedRepoFullName === r.full_name;
                                return (
                                  <li key={r.full_name}>
                                    <button
                                      type="button"
                                      onClick={() => pickProviderRepo(r)}
                                      className={`flex w-full items-start justify-between gap-3 px-3 py-2 text-left text-sm transition-colors hover:bg-accent/50 ${
                                        isSelected ? "bg-primary/10" : ""
                                      }`}
                                    >
                                      <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-1.5">
                                          <span className="truncate font-medium">
                                            {r.full_name}
                                          </span>
                                          {r.private && (
                                            <Lock
                                              className="h-3 w-3 shrink-0 text-muted-foreground"
                                              aria-label="private"
                                            />
                                          )}
                                          {isSelected && (
                                            <CheckCircle2
                                              className="h-3.5 w-3.5 shrink-0 text-primary"
                                              aria-label="selected"
                                            />
                                          )}
                                        </div>
                                        {r.description && (
                                          <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                                            {r.description}
                                          </div>
                                        )}
                                        <div className="mt-0.5 flex items-center gap-3 text-xs text-muted-foreground">
                                          <span className="inline-flex items-center gap-1">
                                            <GitBranch className="h-3 w-3" />
                                            {r.default_branch}
                                          </span>
                                          {r.updated_at && (
                                            <span>
                                              updated{" "}
                                              {formatDistanceToNow(
                                                new Date(r.updated_at),
                                                { addSuffix: true },
                                              )}
                                            </span>
                                          )}
                                        </div>
                                      </div>
                                      {r.html_url && (
                                        <a
                                          href={r.html_url}
                                          target="_blank"
                                          rel="noreferrer"
                                          onClick={(e) => e.stopPropagation()}
                                          className="shrink-0 text-muted-foreground hover:text-foreground"
                                          title="Open on provider"
                                        >
                                          <ExternalLink className="h-3.5 w-3.5" />
                                        </a>
                                      )}
                                    </button>
                                  </li>
                                );
                              })}
                            </ul>
                          );
                        })()
                      )}
                    </div>
                    {pickedRepoFullName && (
                      <p className="text-xs text-muted-foreground">
                        Will link{" "}
                        <span className="font-mono text-foreground">
                          {pickedRepoFullName}
                        </span>{" "}
                        <span className="break-all">
                          ({linkForm.repo_url})
                        </span>
                      </p>
                    )}
                  </div>
                ) : (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <label className="text-sm font-medium">
                        Repository URL
                      </label>
                      {linkForm.connection_id &&
                        connectionFor(linkForm.connection_id)
                          ?.provider_type !== "generic" && (
                          <button
                            type="button"
                            onClick={() => {
                              setManualMode(false);
                              setProviderReposError(null);
                            }}
                            className="text-xs text-muted-foreground underline hover:text-foreground"
                          >
                            Browse repositories instead
                          </button>
                        )}
                    </div>
                    <Input
                      placeholder="https://github.com/acme/web"
                      value={linkForm.repo_url}
                      onChange={(e) =>
                        setLinkForm((p) => ({
                          ...p,
                          repo_url: e.target.value,
                        }))
                      }
                      autoFocus
                    />
                  </div>
                )}

                <div className="space-y-2">
                  <label className="text-sm font-medium">Default branch</label>
                  <Input
                    placeholder="main"
                    value={linkForm.default_branch}
                    onChange={(e) =>
                      setLinkForm((p) => ({
                        ...p,
                        default_branch: e.target.value,
                      }))
                    }
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    Display name{" "}
                    <span className="text-muted-foreground">(optional)</span>
                  </label>
                  <Input
                    placeholder="web"
                    value={linkForm.display_name}
                    onChange={(e) =>
                      setLinkForm((p) => ({
                        ...p,
                        display_name: e.target.value,
                      }))
                    }
                  />
                </div>
                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setLinkOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={submitting}>
                    {submitting ? "Linking\u2026" : "Link repository"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
        </CardHeader>
        <CardContent>
          {!hasConnections && !connectionsError && !loading && (
            <div className="mb-4 rounded-md border border-amber-500/40 bg-amber-500/5 p-3 text-sm text-amber-700 dark:text-amber-400">
              No Git connections are registered yet. An admin needs to add one
              in{" "}
              <Link href="/integrations" className="font-medium underline">
                Integrations
              </Link>
              .
            </div>
          )}
          {connectionsError && !loading && (
            <div className="mb-4 rounded-md border border-muted-foreground/20 bg-muted/50 p-3 text-sm text-muted-foreground">
              You don&apos;t have permission to list Git connections. Only the
              connections already used by this project are shown.
            </div>
          )}

          {loading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : repositories.length === 0 ? (
            <div className="py-10 text-center">
              <Link2 className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                No repositories linked yet.
              </p>
            </div>
          ) : (
            <div className="-mx-2 overflow-x-auto px-2">
              <table className="w-full min-w-[640px] text-sm">
                <thead>
                  <tr className="border-b text-left text-muted-foreground">
                    <th className="pb-3 pr-4 font-medium">Repository</th>
                    <th className="pb-3 pr-4 font-medium">Connection</th>
                    <th className="hidden pb-3 pr-4 font-medium sm:table-cell">
                      Branch
                    </th>
                    <th className="pb-3 pr-4 font-medium">Last event</th>
                    <th className="pb-3 text-right font-medium">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {repositories.map((repo) => {
                    const conn = connectionFor(repo.connection_id);
                    const providerType = conn?.provider_type || "generic";
                    return (
                      <tr key={repo.id} className="border-b last:border-0">
                        <td className="py-3 pr-4">
                          <div className="font-medium">
                            {repo.display_name || repo.repo_url}
                          </div>
                          {repo.display_name && (
                            <div className="mt-0.5 break-all text-xs text-muted-foreground">
                              {repo.repo_url}
                            </div>
                          )}
                        </td>
                        <td className="py-3 pr-4">
                          {conn ? (
                            <span className="inline-flex items-center gap-1.5">
                              <Globe className="h-3.5 w-3.5 text-muted-foreground" />
                              <span className="font-medium">{conn.name}</span>
                              <span className="text-xs text-muted-foreground">
                                ({providerLabel(providerType)})
                              </span>
                            </span>
                          ) : (
                            <span className="text-muted-foreground">
                              {repo.connection_id.slice(0, 8)}{"\u2026"}
                            </span>
                          )}
                        </td>
                        <td className="hidden py-3 pr-4 sm:table-cell">
                          <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                            {repo.default_branch}
                          </code>
                        </td>
                        <td className="py-3 pr-4">
                          {repo.last_event_at ? (
                            <div className="flex items-center gap-2">
                              <Badge variant={statusTone(repo.last_event_status)}>
                                {repo.last_event_status || "received"}
                              </Badge>
                              <span className="text-xs text-muted-foreground">
                                {formatDistanceToNow(
                                  new Date(repo.last_event_at),
                                  { addSuffix: true },
                                )}
                              </span>
                            </div>
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              No deliveries yet
                            </span>
                          )}
                        </td>
                        <td className="py-3">
                          <div className="flex items-center justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => openWebhook(repo)}
                              title="Webhook setup"
                            >
                              <KeyRound className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8"
                              onClick={() => setDeliveriesRepo(repo)}
                              title="Deliveries"
                            >
                              <Activity className="h-4 w-4" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-8 w-8 text-destructive"
                              onClick={() => handleUnlink(repo)}
                              title="Unlink"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <WebhookSetupDialog
        open={webhookRepo !== null}
        onOpenChange={(v) => {
          if (!v) {
            setWebhookRepo(null);
            setWebhookSecret(null);
            setWebhookUrl(null);
          }
        }}
        projectId={projectId}
        repo={webhookRepo}
        providerType={
          webhookRepo
            ? connectionFor(webhookRepo.connection_id)?.provider_type ||
              "generic"
            : "generic"
        }
        initialSecret={webhookSecret}
        initialUrl={webhookUrl}
        onRotated={(updated) => {
          setWebhookSecret(updated.webhook_secret);
          setWebhookUrl(updated.webhook_url);
          loadRepositories();
        }}
      />

      <DeliveriesDialog
        open={deliveriesRepo !== null}
        onOpenChange={(v) => {
          if (!v) setDeliveriesRepo(null);
        }}
        projectId={projectId}
        repo={deliveriesRepo}
      />
    </div>
  );
}
