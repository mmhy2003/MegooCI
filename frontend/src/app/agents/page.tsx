"use client";

import * as React from "react";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  Server,
  Plus,
  Trash2,
  Copy,
  Check,
  Cpu,
  KeyRound,
  Monitor,
  RefreshCw,
  Tag,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { useAuthStore } from "@/lib/auth";
import {
  agentsApi,
  systemApi,
  type Agent,
  type AgentRegistrationResponse,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

function agentStatusVariant(
  s: string,
): "success" | "failed" | "running" | "pending" | "cancelled" {
  if (s === "online") return "success";
  if (s === "busy") return "running";
  if (s === "offline") return "cancelled";
  return "pending";
}

// SnippetBlock renders a labeled `<pre>` with its own "Copy" button so users
// can grab the exact invocation for whichever runtime (binary / Docker /
// Makefile) matches their deployment.
function SnippetBlock({
  title,
  description,
  snippet,
}: {
  title: string;
  description?: React.ReactNode;
  snippet: string;
}) {
  const [copied, setCopied] = React.useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("Clipboard access denied");
    }
  }

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {title}
          </div>
          {description && (
            <div className="text-xs text-muted-foreground">{description}</div>
          )}
        </div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={copy}
          className="shrink-0"
        >
          {copied ? (
            <>
              <Check className="mr-1.5 h-3.5 w-3.5 text-emerald-500" /> Copied
            </>
          ) : (
            <>
              <Copy className="mr-1.5 h-3.5 w-3.5" /> Copy
            </>
          )}
        </Button>
      </div>
      <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs leading-5">
        <code>{snippet}</code>
      </pre>
    </div>
  );
}

export default function AgentsPage() {
  const { user } = useAuthStore();
  const confirm = useConfirm();
  const [agents, setAgents] = React.useState<Agent[]>([]);
  const [loading, setLoading] = React.useState(true);

  // Register dialog state
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [newName, setNewName] = React.useState("");
  const [newLabels, setNewLabels] = React.useState("");
  const [newOs, setNewOs] = React.useState("");
  const [newArch, setNewArch] = React.useState("");
  const [newCapacity, setNewCapacity] = React.useState("1");
  const [creating, setCreating] = React.useState(false);

  // Registration result
  const [justRegistered, setJustRegistered] =
    React.useState<AgentRegistrationResponse | null>(null);
  const [tokenCopied, setTokenCopied] = React.useState(false);

  // Controller URL the snippets suggest to the user. Seeded from
  // `MEGOOCI_PUBLIC_URL` via /api/v1/system/info (which is what external
  // agents should use) and toggled by the "Where will the agent run?"
  // picker — see DEPLOY_MODE comment below.
  const [publicUrl, setPublicUrl] = React.useState<string | null>(null);

  // "compose" = agent shares the Docker network with MegooCI (uses
  // `http://backend:8000` + `--network megooci_default`).
  // "remote"  = agent runs on a separate host and reaches MegooCI via
  // its public URL (no Docker network override).
  const [deployMode, setDeployMode] = React.useState<"compose" | "remote">(
    "compose",
  );
  // Editable by the user so they can paste a custom reverse-proxy URL.
  const [controllerOverride, setControllerOverride] = React.useState("");

  const isAdmin = user?.is_admin ?? false;

  async function loadAgents() {
    try {
      const data = await agentsApi.list();
      setAgents(data);
    } catch {
      toast.error("Failed to load agents");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => {
    loadAgents();
    const interval = setInterval(loadAgents, 15_000);
    return () => clearInterval(interval);
  }, []);

  // Fetch the backend's public URL once so the snippets default to the
  // address that works from outside the Compose network. Failure is
  // non-fatal; we just fall back to the window origin.
  React.useEffect(() => {
    systemApi
      .info()
      .then((info) => setPublicUrl(info.public_url || null))
      .catch(() => {
        /* non-fatal; snippets fall back to `http://localhost:8000` */
      });
  }, []);

  function resetForm() {
    setNewName("");
    setNewLabels("");
    setNewOs("");
    setNewArch("");
    setNewCapacity("1");
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) {
      toast.error("Agent name is required");
      return;
    }
    setCreating(true);
    try {
      const created = await agentsApi.create({
        name: newName.trim(),
        labels: newLabels
          .split(",")
          .map((l) => l.trim())
          .filter(Boolean),
        os: newOs.trim() || undefined,
        arch: newArch.trim() || undefined,
        capacity: Math.max(1, parseInt(newCapacity, 10) || 1),
      });
      setAgents((prev) => [...prev, created]);
      setDialogOpen(false);
      resetForm();
      setJustRegistered(created);
      setTokenCopied(false);
      toast.success("Agent registered");
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Failed to register agent";
      toast.error(msg);
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete(id: string, name: string) {
    const ok = await confirm({
      title: `Delete agent '${name}'?`,
      description:
        "This agent will be disconnected and permanently removed. This action cannot be undone.",
      confirmText: "Delete agent",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await agentsApi.delete(id);
      setAgents((prev) => prev.filter((a) => a.id !== id));
      toast.success("Agent deleted");
    } catch {
      toast.error("Failed to delete agent");
    }
  }

  async function handleRotate(id: string, name: string) {
    const ok = await confirm({
      title: `Rotate token for '${name}'?`,
      description: (
        <>
          The current token will be invalidated immediately. Any running agent
          using the old token will be disconnected at its next reconnect and
          must be restarted with the new token.
        </>
      ),
      confirmText: "Rotate token",
      cancelText: "Keep current",
      tone: "warning",
    });
    if (!ok) return;
    try {
      const updated = await agentsApi.rotateToken(id);
      setAgents((prev) =>
        prev.map((a) => (a.id === id ? { ...a, ...updated } : a)),
      );
      // Reuse the same one-shot card as registration to show the new token.
      setJustRegistered(updated);
      setTokenCopied(false);
      toast.success("Token rotated");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to rotate token";
      toast.error(message);
    }
  }

  async function copyToken() {
    if (!justRegistered) return;
    try {
      await navigator.clipboard.writeText(justRegistered.registration_token);
      setTokenCopied(true);
      setTimeout(() => setTokenCopied(false), 2000);
    } catch {
      toast.error("Clipboard access denied");
    }
  }

  const statusCounts = agents.reduce(
    (acc, a) => {
      acc[a.status] = (acc[a.status] || 0) + 1;
      return acc;
    },
    {} as Record<string, number>,
  );

  return (
    <AppLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
              Agents
            </h1>
            <p className="text-sm text-muted-foreground sm:text-base">
              Build agents that execute pipeline steps.{" "}
              {agents.length > 0 && (
                <span className="ml-1 block text-xs sm:inline">
                  {statusCounts["online"] || 0} online,{" "}
                  {statusCounts["busy"] || 0} busy,{" "}
                  {statusCounts["offline"] || 0} offline
                </span>
              )}
            </p>
          </div>
          {isAdmin && (
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <Button
                onClick={() => setDialogOpen(true)}
                className="w-full sm:w-auto"
              >
                <Plus className="mr-1.5 h-4 w-4" />
                Register Agent
              </Button>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Register a new agent</DialogTitle>
                  <DialogDescription>
                    Define where builds will run. You&apos;ll get a one-time
                    registration token to configure the agent binary.
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleCreate} className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Name</label>
                    <Input
                      placeholder="linux-docker-01"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      autoFocus
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">
                      Labels{" "}
                      <span className="text-muted-foreground">
                        (comma-separated)
                      </span>
                    </label>
                    <Input
                      placeholder="linux, docker, x86_64"
                      value={newLabels}
                      onChange={(e) => setNewLabels(e.target.value)}
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                      <label className="text-sm font-medium">OS</label>
                      <Input
                        placeholder="linux"
                        value={newOs}
                        onChange={(e) => setNewOs(e.target.value)}
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">
                        Architecture
                      </label>
                      <Input
                        placeholder="amd64"
                        value={newArch}
                        onChange={(e) => setNewArch(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">
                      Concurrent capacity
                    </label>
                    <Input
                      type="number"
                      min={1}
                      max={64}
                      value={newCapacity}
                      onChange={(e) => setNewCapacity(e.target.value)}
                    />
                  </div>
                  <DialogFooter>
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setDialogOpen(false)}
                    >
                      Cancel
                    </Button>
                    <Button type="submit" disabled={creating}>
                      {creating ? "Registering…" : "Register Agent"}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          )}
        </div>

        {/* Registration token (shown once) */}
        {justRegistered && (() => {
          // Pick a sensible default controller URL per deployment mode:
          //   compose -> internal Docker DNS `backend:8000` (reachable
          //              only inside `megooci_default`).
          //   remote  -> MEGOOCI_PUBLIC_URL as reported by the backend,
          //              falling back to localhost:8000 if unavailable.
          // Users can still override with the text field below.
          const presetUrl =
            deployMode === "compose"
              ? "http://backend:8000"
              : publicUrl || "http://localhost:8000";
          const controller = controllerOverride.trim() || presetUrl;

          // --network flag is only right for an agent running on the same
          // host as the Compose stack; remote hosts use default bridge
          // networking, so we omit the flag entirely.
          const networkFlag =
            deployMode === "compose" ? "\n  --network megooci_default \\" : "";

          const binarySnippet = `megooci-agent run \\
  --controller ${controller} \\
  --agent-id ${justRegistered.id} \\
  --token ${justRegistered.registration_token}`;

          const dockerSnippet = `docker run -d --name megooci-agent \\
  --restart unless-stopped \\${networkFlag}
  megooci/agent:latest \\
  run \\
    --controller ${controller} \\
    --agent-id ${justRegistered.id} \\
    --token ${justRegistered.registration_token}`;

          const makeSnippet = `make agent-up \\
  ID=${justRegistered.id} \\
  TOKEN=${justRegistered.registration_token}`;

          return (
            <Card className="border-primary/50 bg-primary/5">
              <CardHeader>
                <CardTitle className="text-base">
                  Agent token for {justRegistered.name}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-5">
                <p className="text-sm text-muted-foreground">
                  Copy this token now — it will not be shown again. Pick one
                  of the examples below to connect the agent. Previous
                  tokens (if any) are invalidated immediately.
                </p>

                {/* Deployment mode picker — flips the controller URL and
                    the Docker --network flag across all three snippets. */}
                <div className="space-y-2">
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Where will the agent run?
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setDeployMode("compose");
                        setControllerOverride("");
                      }}
                      className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
                        deployMode === "compose"
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-input hover:bg-accent"
                      }`}
                    >
                      Same host as MegooCI (Compose network)
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setDeployMode("remote");
                        setControllerOverride("");
                      }}
                      className={`rounded-md border px-3 py-1.5 text-sm transition-colors ${
                        deployMode === "remote"
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-input hover:bg-accent"
                      }`}
                    >
                      A different host
                    </button>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs font-medium text-muted-foreground">
                      Controller URL{" "}
                      <span className="text-muted-foreground/70">
                        (override if your MegooCI is behind a reverse proxy)
                      </span>
                    </label>
                    <Input
                      value={controllerOverride}
                      onChange={(e) => setControllerOverride(e.target.value)}
                      placeholder={presetUrl}
                      className="font-mono text-xs"
                    />
                  </div>
                </div>

                {/* Token itself, with its own copy button. */}
                <div className="space-y-1.5">
                  <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Token
                  </div>
                  <div className="flex items-center gap-2">
                    <code className="flex-1 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">
                      {justRegistered.registration_token}
                    </code>
                    <Button size="sm" variant="outline" onClick={copyToken}>
                      {tokenCopied ? (
                        <>
                          <Check className="mr-1.5 h-4 w-4 text-emerald-500" />{" "}
                          Copied
                        </>
                      ) : (
                        <>
                          <Copy className="mr-1.5 h-4 w-4" /> Copy
                        </>
                      )}
                    </Button>
                  </div>
                </div>

                {/* Three ready-to-run invocations, each copyable. */}
                <SnippetBlock
                  title="Run the binary"
                  description="Single Go binary on any Linux / macOS / Windows host. Requires no container runtime."
                  snippet={binarySnippet}
                />

                <SnippetBlock
                  title="Run via Docker"
                  description={
                    deployMode === "compose" ? (
                      <>
                        Uses the prebuilt{" "}
                        <code className="rounded bg-muted px-1 py-0.5 text-[10px]">
                          megooci/agent:latest
                        </code>{" "}
                        image on the Compose network so it can reach the
                        backend by service name.
                      </>
                    ) : (
                      <>
                        Uses the prebuilt{" "}
                        <code className="rounded bg-muted px-1 py-0.5 text-[10px]">
                          megooci/agent:latest
                        </code>{" "}
                        image on the host&apos;s default bridge network; the
                        agent reaches MegooCI via its public URL.
                      </>
                    )
                  }
                  snippet={dockerSnippet}
                />

                {deployMode === "compose" && (
                  <SnippetBlock
                    title="Using the stack Makefile"
                    description={
                      <>
                        Shortest path if you run MegooCI via{" "}
                        <code className="rounded bg-muted px-1 py-0.5 text-[10px]">
                          make up
                        </code>
                        . Builds the image if needed and runs the container
                        on the Compose network.
                      </>
                    }
                    snippet={makeSnippet}
                  />
                )}

                <div className="flex justify-end">
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setJustRegistered(null)}
                  >
                    Dismiss
                  </Button>
                </div>
              </CardContent>
            </Card>
          );
        })()}

        {/* Agents grid */}
        {loading ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={i}>
                <CardHeader>
                  <Skeleton className="h-5 w-32" />
                  <Skeleton className="h-4 w-24" />
                </CardHeader>
                <CardContent className="space-y-2">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-4 w-20" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : agents.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-16">
              <div className="mb-4 rounded-full bg-muted p-4">
                <Server className="h-8 w-8 text-muted-foreground" />
              </div>
              <h3 className="mb-1 text-lg font-semibold">No agents yet</h3>
              <p className="mb-6 max-w-sm text-center text-sm text-muted-foreground">
                Agents are self-hosted workers that execute your pipeline
                steps. Register your first agent to start running builds.
              </p>
              {isAdmin ? (
                <Button onClick={() => setDialogOpen(true)}>
                  <Plus className="mr-1.5 h-4 w-4" />
                  Register Agent
                </Button>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Ask an administrator to register an agent.
                </p>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {agents.map((agent) => (
              <Card key={agent.id} className="transition-shadow hover:shadow-lg">
                <CardHeader>
                  <div className="flex items-start justify-between">
                    <div className="min-w-0">
                      <CardTitle className="truncate text-base">
                        {agent.name}
                      </CardTitle>
                      <p className="mt-0.5 text-xs text-muted-foreground">
                        capacity {agent.capacity}
                      </p>
                    </div>
                    <Badge variant={agentStatusVariant(agent.status)}>
                      {agent.status}
                    </Badge>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  {(agent.os || agent.arch) && (
                    <div className="flex items-center gap-1.5 text-muted-foreground">
                      <Monitor className="h-3.5 w-3.5" />
                      <span>
                        {agent.os || "—"}
                        {agent.arch ? ` / ${agent.arch}` : ""}
                      </span>
                    </div>
                  )}
                  <div className="flex items-center gap-1.5 text-muted-foreground">
                    <Cpu className="h-3.5 w-3.5" />
                    <span>{agent.capacity} concurrent executor(s)</span>
                  </div>
                  {agent.labels.length > 0 && (
                    <div className="flex flex-wrap items-center gap-1">
                      <Tag className="mr-0.5 h-3.5 w-3.5 text-muted-foreground" />
                      {agent.labels.map((label) => (
                        <Badge
                          key={label}
                          variant="secondary"
                          className="text-xs"
                        >
                          {label}
                        </Badge>
                      ))}
                    </div>
                  )}
                  {agent.token_prefix && (
                    <div className="flex items-center gap-1.5 text-muted-foreground">
                      <KeyRound className="h-3.5 w-3.5" />
                      <code className="truncate font-mono text-xs">
                        {agent.token_prefix}
                        {"\u2026"}
                      </code>
                    </div>
                  )}
                  {agent.agent_version && (
                    <div className="text-xs text-muted-foreground">
                      Binary: {agent.agent_version}
                    </div>
                  )}
                  <p className="text-xs text-muted-foreground">
                    {agent.last_seen_at
                      ? `Last seen ${formatDistanceToNow(new Date(agent.last_seen_at), { addSuffix: true })}`
                      : "Never connected"}
                  </p>
                  {isAdmin && (
                    <div className="flex flex-wrap justify-end gap-1 pt-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleRotate(agent.id, agent.name)}
                      >
                        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                        Rotate token
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-destructive hover:text-destructive"
                        onClick={() => handleDelete(agent.id, agent.name)}
                      >
                        <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                        Delete
                      </Button>
                    </div>
                  )}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>
    </AppLayout>
  );
}
