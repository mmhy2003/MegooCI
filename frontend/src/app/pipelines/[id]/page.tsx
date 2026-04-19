"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  ArrowLeft,
  Play,
  Pencil,
  Trash2,
  GitBranch,
  Clock,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import {
  pipelinesApi,
  buildsApi,
  type Pipeline,
  type Build,
  type BuildStatus,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";

function statusVariant(
  s: BuildStatus,
): "success" | "failed" | "running" | "pending" | "cancelled" {
  const map: Record<
    BuildStatus,
    "success" | "failed" | "running" | "pending" | "cancelled"
  > = {
    pending: "pending",
    queued: "pending",
    running: "running",
    success: "success",
    failed: "failed",
    cancelled: "cancelled",
  };
  return map[s];
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const s = new Date(start).getTime();
  const e = end ? new Date(end).getTime() : Date.now();
  const secs = Math.floor((e - s) / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  return `${mins}m ${secs % 60}s`;
}

type Tab = "overview" | "builds" | "configuration";

export default function PipelineDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;

  const [pipeline, setPipeline] = React.useState<Pipeline | null>(null);
  const [builds, setBuilds] = React.useState<Build[]>([]);
  const [activeTab, setActiveTab] = React.useState<Tab>("overview");
  const [loading, setLoading] = React.useState(true);
  const [editing, setEditing] = React.useState(false);
  const [editContent, setEditContent] = React.useState("");
  const [saving, setSaving] = React.useState(false);

  React.useEffect(() => {
    async function load() {
      try {
        const [p, b] = await Promise.all([
          pipelinesApi.get(id),
          buildsApi.list({ pipeline_id: id, limit: 20 }),
        ]);
        setPipeline(p);
        setBuilds(b);
        setEditContent(p.yaml_content || "");
      } catch {
        toast.error("Failed to load pipeline");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [id]);

  async function handleTrigger() {
    try {
      await buildsApi.trigger(id);
      toast.success("Build triggered!");
      const b = await buildsApi.list({ pipeline_id: id, limit: 20 });
      setBuilds(b);
    } catch {
      toast.error("Failed to trigger build");
    }
  }

  async function handleDelete() {
    if (!confirm("Are you sure you want to delete this pipeline?")) return;
    try {
      await pipelinesApi.delete(id);
      toast.success("Pipeline deleted");
      router.push("/pipelines");
    } catch {
      toast.error("Failed to delete pipeline");
    }
  }

  async function handleSaveConfig() {
    setSaving(true);
    try {
      const updated = await pipelinesApi.update(id, {
        yaml_content: editContent,
      });
      setPipeline(updated);
      setEditing(false);
      toast.success("Configuration saved");
    } catch {
      toast.error("Failed to save configuration");
    } finally {
      setSaving(false);
    }
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: "overview", label: "Overview" },
    { key: "builds", label: "Builds" },
    { key: "configuration", label: "Configuration" },
  ];

  if (loading) {
    return (
      <AppLayout>
        <div className="space-y-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-48" />
          <div className="grid gap-4 sm:grid-cols-3">
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
          </div>
        </div>
      </AppLayout>
    );
  }

  if (!pipeline) {
    return (
      <AppLayout>
        <div className="py-16 text-center">
          <p className="text-muted-foreground">Pipeline not found.</p>
          <Button
            variant="link"
            className="mt-2"
            onClick={() => router.push("/pipelines")}
          >
            Back to Pipelines
          </Button>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push("/pipelines")}
        >
          <ArrowLeft className="mr-1.5 h-4 w-4" />
          Pipelines
        </Button>

        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold">{pipeline.name}</h1>
              <Badge variant="secondary">
                {pipeline.definition_format.toUpperCase()}
              </Badge>
              <Badge variant={pipeline.enabled ? "success" : "pending"}>
                {pipeline.enabled ? "Active" : "Inactive"}
              </Badge>
            </div>
            {pipeline.source_repo_url && (
              <p className="mt-1 text-sm text-muted-foreground">
                {pipeline.source_repo_url}
              </p>
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={handleTrigger}>
              <Play className="mr-1.5 h-4 w-4" />
              Trigger Build
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="text-destructive"
              onClick={handleDelete}
            >
              <Trash2 className="mr-1.5 h-4 w-4" />
              Delete
            </Button>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 border-b">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === tab.key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Overview */}
        {activeTab === "overview" && (
          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Pipeline Info</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Project</span>
                  <span className="font-mono text-xs">
                    {pipeline.project_id.slice(0, 12)}…
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Format</span>
                  <span>{pipeline.definition_format.toUpperCase()}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Branch</span>
                  <span>{pipeline.default_branch}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Created</span>
                  <span>
                    {formatDistanceToNow(new Date(pipeline.created_at), {
                      addSuffix: true,
                    })}
                  </span>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Recent Builds</CardTitle>
              </CardHeader>
              <CardContent>
                {builds.length === 0 ? (
                  <p className="py-6 text-center text-sm text-muted-foreground">
                    No builds yet
                  </p>
                ) : (
                  <div className="space-y-2">
                    {builds.slice(0, 5).map((build) => (
                      <div
                        key={build.id}
                        className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm hover:bg-muted/50 cursor-pointer transition-colors"
                        onClick={() => router.push(`/builds/${build.id}`)}
                      >
                        <div className="flex items-center gap-3">
                          <Badge
                            variant={statusVariant(build.status)}
                            className="w-18 justify-center"
                          >
                            {build.status}
                          </Badge>
                          <span className="font-medium">#{build.number}</span>
                        </div>
                        <div className="flex items-center gap-4 text-muted-foreground">
                          <span className="flex items-center gap-1">
                            <GitBranch className="h-3 w-3" />
                            {build.branch || "—"}
                          </span>
                          <span className="flex items-center gap-1">
                            <Clock className="h-3 w-3" />
                            {formatDuration(
                              build.started_at,
                              build.finished_at,
                            )}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}

        {/* Builds tab */}
        {activeTab === "builds" && (
          <Card>
            <CardContent className="pt-6">
              {builds.length === 0 ? (
                <p className="py-12 text-center text-sm text-muted-foreground">
                  No builds for this pipeline yet.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="pb-3 pr-4 font-medium">Build</th>
                        <th className="pb-3 pr-4 font-medium">Branch</th>
                        <th className="pb-3 pr-4 font-medium">Status</th>
                        <th className="pb-3 pr-4 font-medium">Duration</th>
                        <th className="pb-3 pr-4 font-medium">Trigger</th>
                        <th className="pb-3 font-medium text-right">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {builds.map((build) => (
                        <tr
                          key={build.id}
                          className="border-b last:border-0 hover:bg-muted/50 cursor-pointer transition-colors"
                          onClick={() => router.push(`/builds/${build.id}`)}
                        >
                          <td className="py-3 pr-4 font-medium">
                            #{build.number}
                          </td>
                          <td className="py-3 pr-4">
                            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                              {build.branch || "—"}
                            </code>
                          </td>
                          <td className="py-3 pr-4">
                            <Badge variant={statusVariant(build.status)}>
                              {build.status}
                            </Badge>
                          </td>
                          <td className="py-3 pr-4 text-muted-foreground">
                            {formatDuration(
                              build.started_at,
                              build.finished_at,
                            )}
                          </td>
                          <td className="py-3 pr-4 text-muted-foreground">
                            {build.trigger_type}
                          </td>
                          <td className="py-3 text-right text-muted-foreground">
                            {formatDistanceToNow(new Date(build.created_at), {
                              addSuffix: true,
                            })}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Configuration tab */}
        {activeTab === "configuration" && (
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="text-base">Pipeline Definition</CardTitle>
              {!editing ? (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEditing(true)}
                >
                  <Pencil className="mr-1.5 h-4 w-4" />
                  Edit
                </Button>
              ) : (
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setEditing(false);
                      setEditContent(pipeline.yaml_content || "");
                    }}
                  >
                    Cancel
                  </Button>
                  <Button
                    size="sm"
                    disabled={saving}
                    onClick={handleSaveConfig}
                  >
                    {saving ? "Saving…" : "Save"}
                  </Button>
                </div>
              )}
            </CardHeader>
            <CardContent>
              {editing ? (
                <Textarea
                  className="min-h-[400px] font-mono text-sm"
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  spellCheck={false}
                />
              ) : (
                <pre className="overflow-x-auto rounded-lg bg-muted p-4 text-sm">
                  <code>{pipeline.yaml_content || "No definition yet."}</code>
                </pre>
              )}
            </CardContent>
          </Card>
        )}
      </div>
    </AppLayout>
  );
}
