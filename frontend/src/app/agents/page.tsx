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
  Monitor,
  Tag,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useAuthStore } from "@/lib/auth";
import {
  agentsApi,
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

export default function AgentsPage() {
  const { user } = useAuthStore();
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
    if (!confirm(`Delete agent '${name}'? This cannot be undone.`)) return;
    try {
      await agentsApi.delete(id);
      setAgents((prev) => prev.filter((a) => a.id !== id));
      toast.success("Agent deleted");
    } catch {
      toast.error("Failed to delete agent");
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
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Agents</h1>
            <p className="text-muted-foreground">
              Build agents that execute pipeline steps.{" "}
              {agents.length > 0 && (
                <span className="ml-1 text-xs">
                  {statusCounts["online"] || 0} online,{" "}
                  {statusCounts["busy"] || 0} busy,{" "}
                  {statusCounts["offline"] || 0} offline
                </span>
              )}
            </p>
          </div>
          {isAdmin && (
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <Button onClick={() => setDialogOpen(true)}>
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
        {justRegistered && (
          <Card className="border-primary/50 bg-primary/5">
            <CardHeader>
              <CardTitle className="text-base">
                Registration token for {justRegistered.name}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm text-muted-foreground">
                Copy this token now — it will not be shown again. Run the
                agent binary with this token to connect it to MegooCI.
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">
                  {justRegistered.registration_token}
                </code>
                <Button size="sm" variant="outline" onClick={copyToken}>
                  {tokenCopied ? (
                    <>
                      <Check className="mr-1.5 h-4 w-4" /> Copied
                    </>
                  ) : (
                    <>
                      <Copy className="mr-1.5 h-4 w-4" /> Copy
                    </>
                  )}
                </Button>
              </div>
              <pre className="overflow-x-auto rounded-md bg-muted p-3 text-xs">
                <code>
                  {`megooci-agent run \\
  --controller ${typeof window !== "undefined" ? window.location.origin : "https://megooci.example.com"} \\
  --agent-id ${justRegistered.id} \\
  --token ${justRegistered.registration_token}`}
                </code>
              </pre>
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
        )}

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
                  <p className="text-xs text-muted-foreground">
                    {agent.last_seen_at
                      ? `Last seen ${formatDistanceToNow(new Date(agent.last_seen_at), { addSuffix: true })}`
                      : "Never connected"}
                  </p>
                  {isAdmin && (
                    <div className="flex justify-end pt-2">
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
