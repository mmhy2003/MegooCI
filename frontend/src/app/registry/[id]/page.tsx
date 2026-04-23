"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import {
  ArrowLeft,
  Container,
  Tag,
  Trash2,
  Copy,
  Check,
  Lock,
  Globe,
  Settings,
  Package,
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
  type ContainerImage,
  type ContainerTag,
  type RegistryEvent,
  type Project,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export default function RepositoryDetailPage() {
  const params = useParams();
  const router = useRouter();
  const repoId = params.id as string;
  const canManage = usePermission("registry.manage");
  const confirm = useConfirm();

  const [loading, setLoading] = React.useState(true);
  const [repo, setRepo] = React.useState<ContainerRepository | null>(null);
  const [project, setProject] = React.useState<Project | null>(null);
  const [images, setImages] = React.useState<ContainerImage[]>([]);
  const [tags, setTags] = React.useState<ContainerTag[]>([]);
  const [events, setEvents] = React.useState<RegistryEvent[]>([]);
  const [registryHost, setRegistryHost] = React.useState("localhost");
  const [activeTab, setActiveTab] = React.useState<"images" | "tags" | "settings" | "activity">("tags");
  const [copied, setCopied] = React.useState<string | null>(null);

  const loadData = React.useCallback(async () => {
    setLoading(true);
    try {
      const [repoData, imgData, tagData, evData, si] = await Promise.all([
        registryApi.getRepository(repoId),
        registryApi.listImages(repoId, { limit: 100 }),
        registryApi.listTags(repoId, { limit: 200 }),
        registryApi.listEvents({ repository_id: repoId, limit: 30 }),
        systemApi.info(),
      ]);
      setRepo(repoData);
      setImages(imgData);
      setTags(tagData);
      setEvents(evData);
      setRegistryHost(si.registry.host);

      const projectData = await projectsApi.get(repoData.project_id);
      setProject(projectData);
    } catch {
      toast.error("Failed to load repository");
    } finally {
      setLoading(false);
    }
  }, [repoId]);

  React.useEffect(() => { loadData(); }, [loadData]);

  const handleDeleteTag = async (tag: ContainerTag) => {
    const ok = await confirm({
      title: "Delete tag?",
      description: `Remove tag "${tag.name}"? The underlying image will remain.`,
      confirmText: "Delete",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await registryApi.deleteTag(tag.id);
      toast.success(`Tag "${tag.name}" deleted`);
      loadData();
    } catch {
      toast.error("Failed to delete tag");
    }
  };

  const handleToggleAnonymous = async () => {
    if (!repo) return;
    try {
      await registryApi.updateRepository(repo.id, {
        allow_anonymous_pull: !repo.allow_anonymous_pull,
      });
      toast.success(repo.allow_anonymous_pull ? "Anonymous pull disabled" : "Anonymous pull enabled");
      loadData();
    } catch {
      toast.error("Failed to update repository");
    }
  };

  const handleToggleImmutable = async () => {
    if (!repo) return;
    try {
      await registryApi.updateRepository(repo.id, {
        immutable_tags: !repo.immutable_tags,
      });
      toast.success(repo.immutable_tags ? "Immutable tags disabled" : "Immutable tags enabled");
      loadData();
    } catch {
      toast.error("Failed to update repository");
    }
  };

  const copyText = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  };

  const imageMap = React.useMemo(() => {
    const m = new Map<string, ContainerImage>();
    for (const img of images) m.set(img.id, img);
    return m;
  }, [images]);

  const repoFullName = project ? `${registryHost}/${project.slug}/${repo?.name}` : "";

  const tabItems = [
    { id: "tags" as const, label: `Tags (${tags.length})` },
    { id: "images" as const, label: `Images (${images.length})` },
    { id: "activity" as const, label: "Activity" },
    ...(canManage ? [{ id: "settings" as const, label: "Settings" }] : []),
  ];

  if (loading) {
    return (
      <AppLayout>
        <div className="space-y-4">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-48" />
        </div>
      </AppLayout>
    );
  }

  if (!repo) {
    return (
      <AppLayout>
        <div className="flex flex-col items-center justify-center py-20">
          <Package className="h-12 w-12 text-muted-foreground/50 mb-4" />
          <p className="text-lg font-semibold">Repository not found</p>
          <Button variant="outline" className="mt-4" onClick={() => router.push("/registry")}>
            Back to Registry
          </Button>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        {/* Header */}
        <div>
          <Button variant="ghost" size="sm" onClick={() => router.push("/registry")} className="mb-2">
            <ArrowLeft className="h-4 w-4 mr-1" /> Registry
          </Button>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold tracking-tight">
              {project?.slug}/{repo.name}
            </h1>
            {repo.allow_anonymous_pull && (
              <Badge variant="outline"><Globe className="h-3 w-3 mr-1" /> Public</Badge>
            )}
            {repo.immutable_tags && (
              <Badge variant="secondary"><Lock className="h-3 w-3 mr-1" /> Immutable tags</Badge>
            )}
          </div>
          <div className="flex items-center gap-2 mt-2">
            <code className="text-xs bg-muted px-2 py-1 rounded font-mono">{repoFullName}</code>
            <Button
              variant="ghost"
              size="icon"
              className="h-6 w-6"
              onClick={() => copyText(`docker pull ${repoFullName}:latest`, "pull")}
              title="Copy pull command"
            >
              {copied === "pull" ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
            </Button>
          </div>
          <div className="flex items-center gap-4 mt-2 text-sm text-muted-foreground">
            <span>{formatBytes(repo.used_bytes)} used</span>
            {repo.quota_bytes && <span>/ {formatBytes(repo.quota_bytes)} quota</span>}
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 overflow-x-auto border-b">
          {tabItems.map((tab) => (
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

        {/* Tags tab */}
        {activeTab === "tags" && (
          <>
            {tags.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-12 text-center">
                  <Tag className="h-10 w-10 text-muted-foreground/50 mb-3" />
                  <p className="text-sm text-muted-foreground">No tags yet</p>
                </CardContent>
              </Card>
            ) : (
              <Card>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="px-4 py-3 font-medium">Tag</th>
                        <th className="px-4 py-3 font-medium">Digest</th>
                        <th className="px-4 py-3 font-medium hidden sm:table-cell">Size</th>
                        <th className="px-4 py-3 font-medium hidden sm:table-cell">Pushed</th>
                        <th className="px-4 py-3 font-medium">Pull Command</th>
                        {canManage && <th className="px-4 py-3 font-medium w-10"></th>}
                      </tr>
                    </thead>
                    <tbody>
                      {tags.map((tag) => {
                        const img = imageMap.get(tag.image_id);
                        const pullCmd = `docker pull ${repoFullName}:${tag.name}`;
                        return (
                          <tr key={tag.id} className="border-b last:border-0 hover:bg-muted/50">
                            <td className="px-4 py-3 font-semibold">
                              <Badge variant="outline">{tag.name}</Badge>
                            </td>
                            <td className="px-4 py-3 font-mono text-xs text-muted-foreground truncate max-w-[180px]">
                              {img?.digest ? img.digest.slice(0, 19) + "…" : "—"}
                            </td>
                            <td className="px-4 py-3 hidden sm:table-cell text-muted-foreground">
                              {img ? formatBytes(img.size_bytes) : "—"}
                            </td>
                            <td className="px-4 py-3 hidden sm:table-cell text-muted-foreground">
                              {formatDistanceToNow(new Date(tag.updated_at || tag.created_at), { addSuffix: true })}
                            </td>
                            <td className="px-4 py-3">
                              <div className="flex items-center gap-1">
                                <code className="text-xs bg-muted px-1.5 py-0.5 rounded font-mono truncate max-w-[240px]">
                                  {pullCmd}
                                </code>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-6 w-6 shrink-0"
                                  onClick={() => copyText(pullCmd, tag.id)}
                                >
                                  {copied === tag.id ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                                </Button>
                              </div>
                            </td>
                            {canManage && (
                              <td className="px-4 py-3">
                                <Button variant="ghost" size="icon" onClick={() => handleDeleteTag(tag)} className="h-7 w-7">
                                  <Trash2 className="h-3.5 w-3.5 text-destructive" />
                                </Button>
                              </td>
                            )}
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </>
        )}

        {/* Images tab */}
        {activeTab === "images" && (
          <>
            {images.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-12 text-center">
                  <Container className="h-10 w-10 text-muted-foreground/50 mb-3" />
                  <p className="text-sm text-muted-foreground">No images yet</p>
                </CardContent>
              </Card>
            ) : (
              <Card>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="px-4 py-3 font-medium">Digest</th>
                        <th className="px-4 py-3 font-medium">Type</th>
                        <th className="px-4 py-3 font-medium">Size</th>
                        <th className="px-4 py-3 font-medium hidden sm:table-cell">Tags</th>
                        <th className="px-4 py-3 font-medium">Pushed</th>
                      </tr>
                    </thead>
                    <tbody>
                      {images.map((img) => {
                        const imgTags = tags.filter((t) => t.image_id === img.id);
                        return (
                          <tr key={img.id} className="border-b last:border-0 hover:bg-muted/50">
                            <td className="px-4 py-3 font-mono text-xs truncate max-w-[240px]">
                              {img.digest}
                            </td>
                            <td className="px-4 py-3">
                              <Badge variant="outline" className="text-[10px]">
                                {img.media_type.split(".").pop()?.replace("+json", "") ?? img.media_type}
                              </Badge>
                            </td>
                            <td className="px-4 py-3">{formatBytes(img.size_bytes)}</td>
                            <td className="px-4 py-3 hidden sm:table-cell">
                              <div className="flex flex-wrap gap-1">
                                {imgTags.map((t) => (
                                  <Badge key={t.id} variant="secondary" className="text-[10px]">{t.name}</Badge>
                                ))}
                                {imgTags.length === 0 && <span className="text-muted-foreground">untagged</span>}
                              </div>
                            </td>
                            <td className="px-4 py-3 text-muted-foreground">
                              {formatDistanceToNow(new Date(img.created_at), { addSuffix: true })}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}
          </>
        )}

        {/* Activity tab */}
        {activeTab === "activity" && (
          <>
            {events.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-12 text-center">
                  <p className="text-sm text-muted-foreground">No events recorded yet</p>
                </CardContent>
              </Card>
            ) : (
              <Card>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="px-4 py-3 font-medium">Event</th>
                        <th className="px-4 py-3 font-medium">Tag</th>
                        <th className="px-4 py-3 font-medium">Digest</th>
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
                          <td className="px-4 py-3">{ev.tag || "—"}</td>
                          <td className="px-4 py-3 font-mono text-xs truncate max-w-[180px]">
                            {ev.digest ? ev.digest.slice(0, 19) + "…" : "—"}
                          </td>
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

        {/* Settings tab */}
        {activeTab === "settings" && canManage && (
          <div className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Repository Settings</CardTitle>
              </CardHeader>
              <CardContent className="space-y-6">
                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium">Anonymous Pull</p>
                    <p className="text-xs text-muted-foreground">
                      Allow unauthenticated users to pull images from this repository
                    </p>
                  </div>
                  <Button
                    variant={repo.allow_anonymous_pull ? "destructive" : "outline"}
                    size="sm"
                    onClick={handleToggleAnonymous}
                  >
                    {repo.allow_anonymous_pull ? (
                      <><Globe className="h-4 w-4 mr-1" /> Disable</>
                    ) : (
                      <><Globe className="h-4 w-4 mr-1" /> Enable</>
                    )}
                  </Button>
                </div>

                <div className="flex items-center justify-between">
                  <div>
                    <p className="text-sm font-medium">Immutable Tags</p>
                    <p className="text-xs text-muted-foreground">
                      Prevent overwriting existing tags (recommended for production)
                    </p>
                  </div>
                  <Button
                    variant={repo.immutable_tags ? "destructive" : "outline"}
                    size="sm"
                    onClick={handleToggleImmutable}
                  >
                    {repo.immutable_tags ? (
                      <><Lock className="h-4 w-4 mr-1" /> Disable</>
                    ) : (
                      <><Lock className="h-4 w-4 mr-1" /> Enable</>
                    )}
                  </Button>
                </div>

                <div>
                  <p className="text-sm font-medium mb-2">Storage Usage</p>
                  <div className="flex items-center gap-4 text-sm">
                    <span>{formatBytes(repo.used_bytes)} used</span>
                    {repo.quota_bytes ? (
                      <>
                        <span className="text-muted-foreground">of {formatBytes(repo.quota_bytes)}</span>
                        <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                          <div
                            className="h-full bg-primary rounded-full transition-all"
                            style={{ width: `${Math.min(100, (repo.used_bytes / repo.quota_bytes) * 100)}%` }}
                          />
                        </div>
                      </>
                    ) : (
                      <span className="text-muted-foreground">No quota set</span>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Quick Reference</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="rounded-lg bg-muted p-4 font-mono text-xs space-y-2">
                  <p className="text-muted-foreground"># Login</p>
                  <p>docker login {registryHost}</p>
                  <p className="text-muted-foreground mt-3"># Pull latest</p>
                  <p>docker pull {repoFullName}:latest</p>
                  <p className="text-muted-foreground mt-3"># Push a new tag</p>
                  <p>docker tag my-image {repoFullName}:v1.0.0</p>
                  <p>docker push {repoFullName}:v1.0.0</p>
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
