"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  ArrowLeft,
  Play,
  Pencil,
  Power,
  Trash2,
  GitBranch,
  Clock,
  Check,
  X,
  FolderKanban,
} from "lucide-react";
import { Input } from "@/components/ui/input";
import { Sheet } from "@/components/ui/sheet";
import { AiAssistantPanel } from "@/components/pipeline/ai-assistant-panel";
import { DocsPanel } from "@/components/pipeline/docs-panel";
import { VarsPanel } from "@/components/pipeline/vars-panel";
import { AppLayout } from "@/components/layout/app-layout";
import { useConfirm } from "@/components/ui/confirm-dialog";
import {
  pipelinesApi,
  projectsApi,
  buildsApi,
  type Pipeline,
  type Project,
  type Build,
  type BuildStatus,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { PipelineEditor } from "@/components/pipeline/pipeline-editor";
import { usePermission } from "@/hooks/use-permission";

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
  const confirm = useConfirm();
  const id = params.id as string;
  const canManagePipelines = usePermission("pipelines.manage");
  const canManageBuilds = usePermission("builds.manage");

  const [pipeline, setPipeline] = React.useState<Pipeline | null>(null);
  const [project, setProject] = React.useState<Project | null>(null);
  const [builds, setBuilds] = React.useState<Build[]>([]);
  const [activeTab, setActiveTab] = React.useState<Tab>("overview");
  const [loading, setLoading] = React.useState(true);
  const [editing, setEditing] = React.useState(false);
  const [editContent, setEditContent] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [editingName, setEditingName] = React.useState(false);
  const [editName, setEditName] = React.useState("");
  const [aiOpen, setAiOpen] = React.useState(false);
  const [docsOpen, setDocsOpen] = React.useState(false);
  const [varsOpen, setVarsOpen] = React.useState(false);
  const [savingName, setSavingName] = React.useState(false);

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
        // Fetch project info for the breadcrumb
        if (p.project_id) {
          projectsApi.get(p.project_id).then(setProject).catch(() => {});
        }
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
      const build = await buildsApi.trigger(id);
      toast.success("Build triggered!");
      router.push(`/builds/${build.id}`);
    } catch {
      toast.error("Failed to trigger build");
    }
  }

  async function handleDelete() {
    const ok = await confirm({
      title: "Delete this pipeline?",
      description: (
        <>
          <span className="font-medium text-foreground">
            {pipeline?.name ?? "This pipeline"}
          </span>{" "}
          and all of its build history will be permanently removed. This action
          cannot be undone.
        </>
      ),
      confirmText: "Delete pipeline",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await pipelinesApi.delete(id);
      toast.success("Pipeline deleted");
      router.push("/pipelines");
    } catch {
      toast.error("Failed to delete pipeline");
    }
  }

  const [togglingEnabled, setTogglingEnabled] = React.useState(false);

  async function handleToggleEnabled() {
    if (!pipeline) return;
    setTogglingEnabled(true);
    try {
      const updated = await pipelinesApi.update(id, {
        enabled: !pipeline.enabled,
      });
      setPipeline(updated);
      toast.success(updated.enabled ? "Pipeline enabled" : "Pipeline disabled");
    } catch {
      toast.error("Failed to update pipeline");
    } finally {
      setTogglingEnabled(false);
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

  async function handleSaveName() {
    const trimmed = editName.trim();
    if (!trimmed || trimmed === pipeline?.name) {
      setEditingName(false);
      return;
    }
    setSavingName(true);
    try {
      const updated = await pipelinesApi.update(id, { name: trimmed });
      setPipeline(updated);
      setEditingName(false);
      toast.success("Pipeline name updated");
    } catch {
      toast.error("Failed to update pipeline name");
    } finally {
      setSavingName(false);
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
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 sm:gap-3">
              {editingName ? (
                <div className="flex items-center gap-1.5">
                  <Input
                    autoFocus
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveName();
                      if (e.key === "Escape") setEditingName(false);
                    }}
                    className="h-8 w-56 text-lg font-bold sm:w-72"
                    disabled={savingName}
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={handleSaveName}
                    disabled={savingName}
                  >
                    <Check className="h-4 w-4" />
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-7 w-7"
                    onClick={() => setEditingName(false)}
                    disabled={savingName}
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ) : (
                <div className="group flex items-center gap-1.5">
                  <h1 className="break-all text-xl font-bold sm:text-2xl">
                    {pipeline.name}
                  </h1>
                  {canManagePipelines && (
                    <button
                      type="button"
                      onClick={() => {
                        setEditName(pipeline.name);
                        setEditingName(true);
                      }}
                      className="rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:text-foreground group-hover:opacity-100"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                  )}
                </div>
              )}
              <Badge variant="secondary">YAML</Badge>
              <button
                type="button"
                onClick={canManagePipelines ? handleToggleEnabled : undefined}
                disabled={togglingEnabled || !canManagePipelines}
                title={
                  canManagePipelines
                    ? pipeline.enabled
                      ? "Click to disable — webhook triggers will be paused"
                      : "Click to enable — webhook triggers will resume"
                    : undefined
                }
                className={canManagePipelines ? "cursor-pointer" : ""}
              >
                <Badge
                  variant={pipeline.enabled ? "success" : "cancelled"}
                  className="gap-1"
                >
                  <Power className="h-3 w-3" />
                  {pipeline.enabled ? "Active" : "Disabled"}
                </Badge>
              </button>
            </div>
            {pipeline.source_repo_url && (
              <p className="mt-1 break-all text-xs text-muted-foreground sm:text-sm">
                {pipeline.source_repo_url}
              </p>
            )}
            {project && (
              <div className="mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground sm:text-sm">
                <FolderKanban className="h-3.5 w-3.5" />
                <span>Project:</span>
                <button
                  type="button"
                  className="font-medium text-primary hover:underline"
                  onClick={() => router.push(`/projects/${project.id}`)}
                >
                  {project.name}
                </button>
              </div>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {canManageBuilds && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleTrigger}
                className="flex-1 sm:flex-none"
              >
                <Play className="mr-1.5 h-4 w-4" />
                Trigger Build
              </Button>
            )}
            {canManagePipelines && (
              <Button
                variant="outline"
                size="sm"
                className="flex-1 text-destructive sm:flex-none"
                onClick={handleDelete}
              >
                <Trash2 className="mr-1.5 h-4 w-4" />
                Delete
              </Button>
            )}
          </div>
        </div>

        {/* Tabs */}
        <div className="-mx-4 flex gap-1 overflow-x-auto border-b px-4 sm:mx-0 sm:px-0">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`shrink-0 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
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
                  <span>YAML</span>
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
                        className="flex flex-col gap-2 rounded-lg border px-3 py-2 text-sm hover:bg-muted/50 cursor-pointer transition-colors sm:flex-row sm:items-center sm:justify-between"
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
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground sm:text-sm">
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
                <div className="-mx-2 overflow-x-auto px-2">
                  <table className="w-full min-w-[520px] text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="pb-3 pr-4 font-medium">Build</th>
                        <th className="hidden pb-3 pr-4 font-medium sm:table-cell">
                          Branch
                        </th>
                        <th className="pb-3 pr-4 font-medium">Status</th>
                        <th className="hidden pb-3 pr-4 font-medium md:table-cell">
                          Duration
                        </th>
                        <th className="hidden pb-3 pr-4 font-medium lg:table-cell">
                          Trigger
                        </th>
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
                          <td className="hidden py-3 pr-4 sm:table-cell">
                            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                              {build.branch || "—"}
                            </code>
                          </td>
                          <td className="py-3 pr-4">
                            <Badge variant={statusVariant(build.status)}>
                              {build.status}
                            </Badge>
                          </td>
                          <td className="hidden py-3 pr-4 text-muted-foreground md:table-cell">
                            {formatDuration(
                              build.started_at,
                              build.finished_at,
                            )}
                          </td>
                          <td className="hidden py-3 pr-4 text-muted-foreground lg:table-cell">
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
            <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
              <CardTitle className="text-base">Pipeline Definition</CardTitle>
              {!editing ? (
                canManagePipelines && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setEditing(true)}
                  >
                    <Pencil className="mr-1.5 h-4 w-4" />
                    Edit
                  </Button>
                )
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
              <PipelineEditor
                value={editing ? editContent : (pipeline.yaml_content || "")}
                onChange={editing ? setEditContent : undefined}
                readOnly={!editing}
                minHeight="400px"
                placeholder="No definition yet."
                aiOpen={aiOpen}
                onToggleAi={() => setAiOpen((prev) => !prev)}
                docsOpen={docsOpen}
                onToggleDocs={() => setDocsOpen((prev) => !prev)}
                varsOpen={varsOpen}
                onToggleVars={() => setVarsOpen((prev) => !prev)}
              />
            </CardContent>
          </Card>
        )}
      </div>

      {/* AI Assistant Drawer */}
      <Sheet open={aiOpen} onOpenChange={setAiOpen}>
        <AiAssistantPanel
          currentYaml={editing ? editContent : (pipeline.yaml_content || "")}
          onApplyYaml={
            editing
              ? (yaml) => setEditContent(yaml)
              : undefined
          }
          projectId={pipeline.project_id}
          onClose={() => setAiOpen(false)}
        />
      </Sheet>

      {/* Docs Drawer */}
      <Sheet open={docsOpen} onOpenChange={setDocsOpen}>
        <DocsPanel
          onInsert={
            editing
              ? (yaml) => {
                  const trimmed = editContent.trimEnd();
                  setEditContent(trimmed ? `${trimmed}\n\n${yaml}\n` : `${yaml}\n`);
                }
              : undefined
          }
          onClose={() => setDocsOpen(false)}
        />
      </Sheet>

      {/* Vars Drawer */}
      <Sheet open={varsOpen} onOpenChange={setVarsOpen}>
        <VarsPanel
          projectId={pipeline.project_id}
          pipelineId={pipeline.id}
          onInsert={
            editing
              ? (snippet) => {
                  const trimmed = editContent.trimEnd();
                  setEditContent(trimmed ? `${trimmed} ${snippet}` : snippet);
                }
              : undefined
          }
          onClose={() => setVarsOpen(false)}
        />
      </Sheet>
    </AppLayout>
  );
}
