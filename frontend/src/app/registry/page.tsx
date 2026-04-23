"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import {
  Container,
  Package,
  Tag,
  HardDrive,
  ArrowRight,
  Trash2,
  KeyRound,
  Plus,
  Copy,
  Check,
  Shield,
  ShieldOff,
  Lock,
} from "lucide-react";
import { toast } from "sonner";
import { AppLayout } from "@/components/layout/app-layout";
import { usePermission } from "@/hooks/use-permission";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  registryApi,
  projectsApi,
  systemApi,
  type ContainerRepository,
  type DeployToken,
  type DeployTokenCreated,
  type RegistryOverview,
  type RegistryEvent,
  type Project,
  type SystemInfo,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export default function RegistryPage() {
  const router = useRouter();
  const canManage = usePermission("registry.manage");
  const confirm = useConfirm();

  const [loading, setLoading] = React.useState(true);
  const [overview, setOverview] = React.useState<RegistryOverview | null>(null);
  const [repos, setRepos] = React.useState<ContainerRepository[]>([]);
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [deployTokens, setDeployTokens] = React.useState<DeployToken[]>([]);
  const [events, setEvents] = React.useState<RegistryEvent[]>([]);
  const [systemInfo, setSystemInfo] = React.useState<SystemInfo | null>(null);
  const [activeTab, setActiveTab] = React.useState<"repositories" | "tokens" | "events">("repositories");

  const [tokenDialogOpen, setTokenDialogOpen] = React.useState(false);
  const [tokenName, setTokenName] = React.useState("");
  const [tokenScope, setTokenScope] = React.useState("pull");
  const [tokenProjectId, setTokenProjectId] = React.useState("");
  const [tokenExpiryDays, setTokenExpiryDays] = React.useState("");
  const [createdToken, setCreatedToken] = React.useState<DeployTokenCreated | null>(null);
  const [copied, setCopied] = React.useState(false);

  const loadData = React.useCallback(async () => {
    setLoading(true);
    try {
      const [ov, rp, pr, dt, ev, si] = await Promise.all([
        registryApi.overview(),
        registryApi.listRepositories({ limit: 100 }),
        projectsApi.list(),
        canManage ? registryApi.listDeployTokens() : Promise.resolve([]),
        registryApi.listEvents({ limit: 20 }),
        systemApi.info(),
      ]);
      setOverview(ov);
      setRepos(rp);
      setProjects(pr);
      setDeployTokens(dt);
      setEvents(ev);
      setSystemInfo(si);
    } catch {
      toast.error("Failed to load registry data");
    } finally {
      setLoading(false);
    }
  }, [canManage]);

  React.useEffect(() => { loadData(); }, [loadData]);

  const handleDeleteRepo = async (repo: ContainerRepository) => {
    const ok = await confirm({
      title: "Delete repository?",
      description: `This will permanently delete "${repo.name}" and all its images and tags.`,
      confirmText: "Delete",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await registryApi.deleteRepository(repo.id);
      toast.success("Repository deleted");
      loadData();
    } catch {
      toast.error("Failed to delete repository");
    }
  };

  const handleCreateToken = async () => {
    if (!tokenName.trim() || !tokenProjectId) return;
    try {
      const created = await registryApi.createDeployToken(tokenProjectId, {
        name: tokenName.trim(),
        scope: tokenScope,
        expires_in_days: tokenExpiryDays ? parseInt(tokenExpiryDays) : undefined,
      });
      setCreatedToken(created);
      toast.success("Deploy token created");
      loadData();
    } catch {
      toast.error("Failed to create deploy token");
    }
  };

  const handleRevokeToken = async (token: DeployToken) => {
    const ok = await confirm({
      title: "Revoke deploy token?",
      description: `"${token.name}" will be immediately disabled.`,
      confirmText: "Revoke",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await registryApi.revokeDeployToken(token.id);
      toast.success("Token revoked");
      loadData();
    } catch {
      toast.error("Failed to revoke token");
    }
  };

  const copyToken = () => {
    if (!createdToken) return;
    navigator.clipboard.writeText(createdToken.token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const projectMap = React.useMemo(() => {
    const m = new Map<string, Project>();
    for (const p of projects) m.set(p.id, p);
    return m;
  }, [projects]);

  const registryHost = systemInfo?.registry?.host || "localhost";

  const tabs = [
    { id: "repositories" as const, label: "Repositories" },
    { id: "tokens" as const, label: "Deploy Tokens" },
    { id: "events" as const, label: "Activity" },
  ];

  return (
    <AppLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Container Registry</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Embedded OCI registry — push and pull Docker images from your pipelines
            </p>
          </div>
        </div>

        {/* Overview stats */}
        {loading ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-24 rounded-xl" />
            ))}
          </div>
        ) : overview && (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Card>
              <CardContent className="flex items-center gap-4 pt-6">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                  <Package className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <p className="text-2xl font-bold">{overview.total_repositories}</p>
                  <p className="text-xs text-muted-foreground">Repositories</p>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-4 pt-6">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                  <Container className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <p className="text-2xl font-bold">{overview.total_images}</p>
                  <p className="text-xs text-muted-foreground">Images</p>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-4 pt-6">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                  <Tag className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <p className="text-2xl font-bold">{overview.total_tags}</p>
                  <p className="text-xs text-muted-foreground">Tags</p>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-4 pt-6">
                <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10">
                  <HardDrive className="h-5 w-5 text-primary" />
                </div>
                <div>
                  <p className="text-2xl font-bold">{formatBytes(overview.total_size_bytes)}</p>
                  <p className="text-xs text-muted-foreground">Total Size</p>
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {/* Tabs */}
        <div className="flex gap-1 overflow-x-auto border-b">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`whitespace-nowrap border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === tab.id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Repositories tab */}
        {activeTab === "repositories" && (
          <>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-20 rounded-xl" />
                ))}
              </div>
            ) : repos.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                  <Package className="h-12 w-12 text-muted-foreground/50 mb-4" />
                  <h3 className="text-lg font-semibold">No repositories yet</h3>
                  <p className="text-sm text-muted-foreground mt-1 max-w-md">
                    Repositories are created automatically when a pipeline pushes an image.
                    Add a <code className="text-xs bg-muted px-1 py-0.5 rounded">docker_push</code> step to get started.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-3">
                {repos.map((repo) => {
                  const project = projectMap.get(repo.project_id);
                  const pullCmd = `docker pull ${registryHost}/${project?.slug ?? "project"}/${repo.name}`;
                  return (
                    <Card key={repo.id} className="hover:border-primary/30 transition-colors">
                      <CardContent className="flex items-center justify-between py-4">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => router.push(`/registry/${repo.id}`)}
                              className="text-sm font-semibold hover:text-primary transition-colors truncate"
                            >
                              {project?.slug ?? "unknown"}/{repo.name}
                            </button>
                            {repo.allow_anonymous_pull && (
                              <Badge variant="outline" className="text-[10px] shrink-0">Public</Badge>
                            )}
                            {repo.immutable_tags && (
                              <Badge variant="secondary" className="text-[10px] shrink-0">
                                <Lock className="h-3 w-3 mr-0.5" /> Immutable
                              </Badge>
                            )}
                          </div>
                          <p className="text-xs text-muted-foreground mt-1 font-mono truncate">
                            {pullCmd}
                          </p>
                          <div className="flex items-center gap-4 mt-1 text-xs text-muted-foreground">
                            <span>{formatBytes(repo.used_bytes)}</span>
                            {repo.quota_bytes && (
                              <span>Quota: {formatBytes(repo.quota_bytes)}</span>
                            )}
                            <span>Created {formatDistanceToNow(new Date(repo.created_at), { addSuffix: true })}</span>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 shrink-0 ml-4">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => router.push(`/registry/${repo.id}`)}
                            title="View repository"
                          >
                            <ArrowRight className="h-4 w-4" />
                          </Button>
                          {canManage && (
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => handleDeleteRepo(repo)}
                              title="Delete repository"
                            >
                              <Trash2 className="h-4 w-4 text-destructive" />
                            </Button>
                          )}
                        </div>
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}
          </>
        )}

        {/* Deploy Tokens tab */}
        {activeTab === "tokens" && (
          <>
            {canManage && (
              <div className="flex justify-end">
                <Button onClick={() => {
                  setTokenDialogOpen(true);
                  setCreatedToken(null);
                  setTokenName("");
                  setTokenScope("pull");
                  setTokenProjectId(projects[0]?.id ?? "");
                  setTokenExpiryDays("");
                }}>
                  <Plus className="h-4 w-4 mr-2" />
                  New Deploy Token
                </Button>
              </div>
            )}

            {deployTokens.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                  <KeyRound className="h-12 w-12 text-muted-foreground/50 mb-4" />
                  <h3 className="text-lg font-semibold">No deploy tokens</h3>
                  <p className="text-sm text-muted-foreground mt-1 max-w-md">
                    Deploy tokens let external servers pull (or push) images without a user account.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <div className="space-y-3">
                {deployTokens.map((dt) => {
                  const project = projectMap.get(dt.project_id);
                  return (
                    <Card key={dt.id}>
                      <CardContent className="flex items-center justify-between py-4">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-semibold">{dt.name}</span>
                            <Badge variant={dt.is_active ? "success" : "destructive"}>
                              {dt.is_active ? "Active" : "Revoked"}
                            </Badge>
                            <Badge variant="outline">{dt.scope}</Badge>
                          </div>
                          <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground">
                            <span>Project: {project?.name ?? dt.project_id}</span>
                            <span>Hint: ...{dt.token_hint}</span>
                            {dt.expires_at && (
                              <span>Expires {formatDistanceToNow(new Date(dt.expires_at), { addSuffix: true })}</span>
                            )}
                            {dt.last_used_at && (
                              <span>Last used {formatDistanceToNow(new Date(dt.last_used_at), { addSuffix: true })}</span>
                            )}
                          </div>
                        </div>
                        {canManage && dt.is_active && (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => handleRevokeToken(dt)}
                            title="Revoke token"
                          >
                            <ShieldOff className="h-4 w-4 text-destructive" />
                          </Button>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            )}

            {/* Create token dialog */}
            <Dialog open={tokenDialogOpen} onOpenChange={setTokenDialogOpen}>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>
                    {createdToken ? "Deploy Token Created" : "Create Deploy Token"}
                  </DialogTitle>
                </DialogHeader>

                {createdToken ? (
                  <div className="space-y-4">
                    <p className="text-sm text-muted-foreground">
                      Copy this token now — it will not be shown again.
                    </p>
                    <div className="flex items-center gap-2">
                      <Input value={createdToken.token} readOnly className="font-mono text-xs" />
                      <Button variant="outline" size="icon" onClick={copyToken}>
                        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                      </Button>
                    </div>
                    <div className="rounded-lg bg-muted p-3 text-xs font-mono space-y-1">
                      <p>docker login {registryHost} -u deploy-token -p {createdToken.token}</p>
                    </div>
                    <DialogFooter>
                      <Button onClick={() => setTokenDialogOpen(false)}>Done</Button>
                    </DialogFooter>
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div>
                      <label className="text-sm font-medium">Name</label>
                      <Input
                        value={tokenName}
                        onChange={(e) => setTokenName(e.target.value)}
                        placeholder="e.g. production-server"
                      />
                    </div>
                    <div>
                      <label className="text-sm font-medium">Project</label>
                      <Select
                        value={tokenProjectId}
                        onChange={(e) => setTokenProjectId(e.target.value)}
                        options={projects.map((p) => ({ value: p.id, label: p.name }))}
                        placeholder="Select project"
                      />
                    </div>
                    <div>
                      <label className="text-sm font-medium">Scope</label>
                      <Select
                        value={tokenScope}
                        onChange={(e) => setTokenScope(e.target.value)}
                        options={[
                          { value: "pull", label: "Pull only" },
                          { value: "push", label: "Pull & Push" },
                        ]}
                      />
                    </div>
                    <div>
                      <label className="text-sm font-medium">Expiry (days, optional)</label>
                      <Input
                        type="number"
                        value={tokenExpiryDays}
                        onChange={(e) => setTokenExpiryDays(e.target.value)}
                        placeholder="Leave empty for no expiry"
                      />
                    </div>
                    <DialogFooter>
                      <Button variant="outline" onClick={() => setTokenDialogOpen(false)}>
                        Cancel
                      </Button>
                      <Button onClick={handleCreateToken} disabled={!tokenName.trim() || !tokenProjectId}>
                        Create
                      </Button>
                    </DialogFooter>
                  </div>
                )}
              </DialogContent>
            </Dialog>
          </>
        )}

        {/* Events tab */}
        {activeTab === "events" && (
          <>
            {events.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                  <Shield className="h-12 w-12 text-muted-foreground/50 mb-4" />
                  <h3 className="text-lg font-semibold">No registry events</h3>
                  <p className="text-sm text-muted-foreground mt-1">
                    Events will appear here when images are pushed or pulled.
                  </p>
                </CardContent>
              </Card>
            ) : (
              <Card>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="px-4 py-3 font-medium">Event</th>
                        <th className="px-4 py-3 font-medium">Digest</th>
                        <th className="px-4 py-3 font-medium">Tag</th>
                        <th className="px-4 py-3 font-medium hidden sm:table-cell">IP</th>
                        <th className="px-4 py-3 font-medium">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {events.map((ev) => (
                        <tr key={ev.id} className="border-b last:border-0 hover:bg-muted/50">
                          <td className="px-4 py-3">
                            <Badge variant={ev.event_type === "image.pushed" ? "success" : ev.event_type === "image.deleted" ? "destructive" : "outline"}>
                              {ev.event_type}
                            </Badge>
                          </td>
                          <td className="px-4 py-3 font-mono text-xs truncate max-w-[200px]">
                            {ev.digest ? ev.digest.slice(0, 19) + "…" : "—"}
                          </td>
                          <td className="px-4 py-3">{ev.tag || "—"}</td>
                          <td className="px-4 py-3 hidden sm:table-cell text-muted-foreground">
                            {ev.ip_address || "—"}
                          </td>
                          <td className="px-4 py-3 text-muted-foreground">
                            {formatDistanceToNow(new Date(ev.created_at), { addSuffix: true })}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </>
        )}
      </div>
    </AppLayout>
  );
}
