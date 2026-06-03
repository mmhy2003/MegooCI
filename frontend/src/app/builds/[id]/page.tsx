"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  ArrowLeft,
  Download,
  FileArchive,
  RotateCw,
  Trash2,
  XCircle,
  GitBranch,
  Clock,
  Hash,
  ShieldCheck,
  ShieldX,
  Loader2,
  FolderKanban,
  Play,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import {
  buildsApi,
  artifactsApi,
  gatesApi,
  pipelinesApi,
  projectsApi,
  type Artifact,
  type BuildDetail,
  type BuildStatus,
  type BuildStage,
  type Pipeline,
  type Project,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StageGraph, type Stage, type StageStatus } from "@/components/stage-graph";
import { BuildLogViewer, type LogLine } from "@/components/build-log-viewer";
import { useWebSocket } from "@/hooks/use-websocket";
import { usePermission } from "@/hooks/use-permission";
import { useAuthStore } from "@/lib/auth";

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

function toStageStatus(s: string): StageStatus {
  if (s === "queued") return "pending";
  if (["pending", "running", "success", "failed", "cancelled"].includes(s))
    return s as StageStatus;
  return "pending";
}

function buildStagesToGraphStages(stages: BuildStage[]): Stage[] {
  return stages
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((s) => ({
      id: s.id,
      name: s.name,
      status: toStageStatus(s.status),
    }));
}

export default function BuildDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  const canManageBuilds = usePermission("builds.manage");
  const { accessToken } = useAuthStore();

  const [build, setBuild] = React.useState<BuildDetail | null>(null);
  const [pipeline, setPipeline] = React.useState<Pipeline | null>(null);
  const [project, setProject] = React.useState<Project | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [selectedStageId, setSelectedStageId] = React.useState<string>("");
  const [artifacts, setArtifacts] = React.useState<Artifact[]>([]);
  const [approvingSteps, setApprovingSteps] = React.useState<Set<string>>(new Set());
  const canManageArtifacts = usePermission("artifacts.manage");

  const isRunning = build?.status === "running" || build?.status === "queued";
  const isActive = isRunning || build?.status === "pending";
  // A build can be cancelled until it reaches a terminal state — while it is
  // still pending (created/awaiting dispatch), queued, or running. Mirrors the
  // backend cancel endpoint, which rejects already-finished builds.
  const canCancel =
    build?.status === "pending" ||
    build?.status === "queued" ||
    build?.status === "running";

  const wsUrl = React.useMemo(() => {
    if (!isActive || !accessToken || typeof window === "undefined") return null;
    const tokenParam = `token=${encodeURIComponent(accessToken)}`;
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
    if (apiBase) {
      const url = new URL(apiBase);
      const wsProto = url.protocol === "https:" ? "wss:" : "ws:";
      return `${wsProto}//${url.host}/api/v1/ws/builds/${id}/logs?${tokenParam}`;
    }
    const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProto}//${window.location.host}/api/v1/ws/builds/${id}/logs?${tokenParam}`;
  }, [isActive, id, accessToken]);
  const { messages } = useWebSocket(wsUrl);

  const [logLines, setLogLines] = React.useState<LogLine[]>([]);

  React.useEffect(() => {
    buildsApi
      .get(id)
      .then((data) => {
        setBuild(data);
        if (data.stages?.length > 0) {
          setSelectedStageId(data.stages[0].id);
        }
        // Fetch pipeline + project info for the header
        pipelinesApi.get(data.pipeline_id).then((pl) => {
          setPipeline(pl);
          if (pl.project_id) {
            projectsApi.get(pl.project_id).then(setProject).catch(() => {});
          }
        }).catch(() => {});
        // For completed builds, load persisted logs from the REST API
        // since there's no live WebSocket stream to connect to.
        const isFinished = ["success", "failed", "cancelled"].includes(data.status);
        if (isFinished) {
          buildsApi.logs(id).then((chunks) => {
            const lines: LogLine[] = chunks.map((c) => ({
              text: c.content.replace(/\n$/, ""),
              timestamp: c.timestamp ?? undefined,
              stream: (c.stream as LogLine["stream"]) || "stdout",
            }));
            if (lines.length > 0) {
              setLogLines(lines);
            }
          }).catch(() => {});
        }
      })
      .catch(() => toast.error("Failed to load build"))
      .finally(() => setLoading(false));

    // Load artifacts independently.
    artifactsApi.list(id).then(setArtifacts).catch(() => {});
  }, [id]);

  React.useEffect(() => {
    const newLogLines: LogLine[] = [];

    for (const msg of messages) {
      try {
        const parsed = JSON.parse(msg);
        const event = parsed.event as string | undefined;

        if (event === "build_started") {
          setBuild((prev) =>
            prev ? { ...prev, status: "running", started_at: new Date().toISOString() } : prev,
          );
        } else if (event === "build_finished") {
          setBuild((prev) =>
            prev
              ? { ...prev, status: parsed.status, finished_at: new Date().toISOString() }
              : prev,
          );
        } else if (event === "stage_started") {
          setBuild((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              stages: prev.stages.map((s) =>
                s.id === parsed.stage_id
                  ? { ...s, status: "running", started_at: new Date().toISOString() }
                  : s,
              ),
            };
          });
        } else if (event === "stage_finished") {
          setBuild((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              stages: prev.stages.map((s) =>
                s.id === parsed.stage_id
                  ? { ...s, status: parsed.status, finished_at: new Date().toISOString() }
                  : s,
              ),
            };
          });
        } else if (event === "step_started") {
          setBuild((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              stages: prev.stages.map((stage) => ({
                ...stage,
                steps: stage.steps.map((step) =>
                  step.id === parsed.step_id
                    ? { ...step, status: "running", started_at: new Date().toISOString() }
                    : step,
                ),
              })),
            };
          });
        } else if (event === "step_finished") {
          setBuild((prev) => {
            if (!prev) return prev;
            return {
              ...prev,
              stages: prev.stages.map((stage) => ({
                ...stage,
                steps: stage.steps.map((step) =>
                  step.id === parsed.step_id
                    ? {
                        ...step,
                        status: parsed.status,
                        exit_code: parsed.exit_code ?? step.exit_code,
                        finished_at: new Date().toISOString(),
                      }
                    : step,
                ),
              })),
            };
          });
        } else if (event === "log") {
          newLogLines.push({
            text: parsed.content || "",
            timestamp: parsed.timestamp,
            stream: parsed.stream || "stdout",
          });
        } else {
          newLogLines.push({
            text: parsed.text || parsed.content || msg,
            timestamp: parsed.timestamp,
            stream: parsed.stream || "stdout",
          });
        }
      } catch {
        newLogLines.push({ text: msg, stream: "stdout" });
      }
    }

    setLogLines(newLogLines);
  }, [messages]);

  async function handleRetry() {
    try {
      const newBuild = await buildsApi.retry(id);
      toast.success("Build re-triggered!");
      router.push(`/builds/${newBuild.id}`);
    } catch {
      toast.error("Failed to re-run build");
    }
  }

  async function handleCancel() {
    try {
      const updated = await buildsApi.cancel(id);
      setBuild((prev) => (prev ? { ...prev, ...updated } : null));
      toast.success("Build cancelled");
    } catch {
      toast.error("Failed to cancel build");
    }
  }

  async function handleDispatch() {
    try {
      const updated = await buildsApi.dispatch(id);
      setBuild((prev) => (prev ? { ...prev, ...updated } : null));
      toast.success("Build dispatched to agent!");
    } catch (err: unknown) {
      const message =
        err instanceof Error ? err.message : "Failed to dispatch build";
      toast.error(message);
    }
  }

  async function handleGateResponse(stepId: string, approved: boolean) {
    setApprovingSteps((prev) => new Set(prev).add(stepId));
    try {
      await gatesApi.resolveInput(stepId, approved);
      toast.success(approved ? "Step approved" : "Step rejected");
    } catch {
      toast.error(`Failed to ${approved ? "approve" : "reject"} step`);
    } finally {
      setApprovingSteps((prev) => {
        const next = new Set(prev);
        next.delete(stepId);
        return next;
      });
    }
  }

  const stages = build?.stages ? buildStagesToGraphStages(build.stages) : [];
  const selectedStage = build?.stages?.find((s) => s.id === selectedStageId);
  const selectedSteps = selectedStage?.steps || [];

  const statusIcon: Record<StageStatus, string> = {
    pending: "○",
    running: "◉",
    success: "✓",
    failed: "✗",
    cancelled: "◌",
  };

  const statusColor: Record<StageStatus, string> = {
    pending: "text-gray-500",
    running: "text-cyan-500",
    success: "text-emerald-500",
    failed: "text-red-500",
    cancelled: "text-yellow-500",
  };

  if (loading) {
    return (
      <AppLayout>
        <div className="space-y-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-48" />
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-[400px] w-full" />
        </div>
      </AppLayout>
    );
  }

  if (!build) {
    return (
      <AppLayout>
        <div className="py-16 text-center">
          <p className="text-muted-foreground">Build not found.</p>
          <Button
            variant="link"
            className="mt-2"
            onClick={() => router.push("/builds")}
          >
            Back to Builds
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
          onClick={() => router.push("/builds")}
        >
          <ArrowLeft className="mr-1.5 h-4 w-4" />
          Builds
        </Button>

        {/* Header */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-xl font-bold sm:text-2xl">
                Build #{build.number}
              </h1>
              <Badge
                variant={statusVariant(build.status)}
                className="text-sm"
              >
                {build.status}
              </Badge>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground sm:text-sm">
              <button
                type="button"
                className="flex items-center gap-1 hover:text-foreground transition-colors"
                onClick={() => router.push(`/pipelines/${build.pipeline_id}`)}
              >
                <Hash className="h-3.5 w-3.5" />
                {pipeline?.name || build.pipeline_id.slice(0, 8) + "…"}
              </button>
              {project && (
                <button
                  type="button"
                  className="flex items-center gap-1 hover:text-foreground transition-colors"
                  onClick={() => router.push(`/projects/${project.id}`)}
                >
                  <FolderKanban className="h-3.5 w-3.5" />
                  {project.name}
                </button>
              )}
              {build.branch && (
                <span className="flex items-center gap-1">
                  <GitBranch className="h-3.5 w-3.5" />
                  {build.branch}
                </span>
              )}
              {build.commit_sha && (
                <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                  {build.commit_sha.slice(0, 7)}
                </code>
              )}
              <span className="flex items-center gap-1">
                <Clock className="h-3.5 w-3.5" />
                {formatDuration(build.started_at, build.finished_at)}
              </span>
              <span>
                {formatDistanceToNow(new Date(build.created_at), {
                  addSuffix: true,
                })}
              </span>
            </div>
          </div>
          {canManageBuilds && (
            <div className="flex flex-wrap gap-2">
              {build.status === "pending" && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleDispatch}
                  className="flex-1 border-cyan-500/30 text-cyan-600 hover:bg-cyan-500/10 hover:text-cyan-700 sm:flex-none"
                >
                  <Play className="mr-1.5 h-4 w-4" />
                  Dispatch
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={handleRetry}
                className="flex-1 sm:flex-none"
              >
                <RotateCw className="mr-1.5 h-4 w-4" />
                Re-run
              </Button>
              {canCancel && (
                <Button
                  variant="outline"
                  size="sm"
                  className="flex-1 text-destructive sm:flex-none"
                  onClick={handleCancel}
                >
                  <XCircle className="mr-1.5 h-4 w-4" />
                  Cancel
                </Button>
              )}
            </div>
          )}
        </div>

        {/* Stage Visualization */}
        {stages.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Pipeline Stages</CardTitle>
            </CardHeader>
            <CardContent>
              <StageGraph
                stages={stages}
                selectedStageId={selectedStageId}
                onSelectStage={setSelectedStageId}
              />
            </CardContent>
          </Card>
        )}

        {/* Steps for selected stage */}
        {selectedStage && (
          <Card>
            <CardHeader>
              <CardTitle className="text-base">
                Steps — {selectedStage.name}
              </CardTitle>
            </CardHeader>
            <CardContent>
              {selectedSteps.length === 0 ? (
                <p className="py-6 text-center text-sm text-muted-foreground">
                  No steps for this stage
                </p>
              ) : (
                <div className="space-y-2">
                  {selectedSteps
                    .sort((a, b) => a.sort_order - b.sort_order)
                    .map((step) => (
                      <div
                        key={step.id}
                        className="flex flex-col gap-2 rounded-lg border px-3 py-3 text-sm sm:px-4"
                      >
                        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                          <div className="flex min-w-0 items-start gap-3 sm:items-center">
                            <span
                              className={`shrink-0 text-lg font-bold ${statusColor[toStageStatus(step.status)]}`}
                            >
                              {statusIcon[toStageStatus(step.status)]}
                            </span>
                            <div className="min-w-0 flex-1">
                              <span className="font-medium">{step.name}</span>
                              {step.command && (
                                <code className="ml-0 mt-1 block break-all text-xs text-muted-foreground sm:ml-2 sm:mt-0 sm:inline">
                                  {step.command}
                                </code>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-3 pl-7 text-xs text-muted-foreground sm:gap-4 sm:pl-0 sm:text-sm">
                            <span>
                              {formatDuration(step.started_at, step.finished_at)}
                            </span>
                            {step.exit_code !== null && (
                              <code
                                className={`rounded px-1.5 py-0.5 text-xs ${
                                  step.exit_code === 0
                                    ? "bg-emerald-500/10 text-emerald-600"
                                    : "bg-red-500/10 text-red-600"
                                }`}
                              >
                                exit {step.exit_code}
                              </code>
                            )}
                          </div>
                        </div>

                        {/* Approval gate for wait_input steps */}
                        {step.step_type === "wait_input" && step.status === "running" && canManageBuilds && (
                          <div className="ml-7 flex flex-col gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-4 py-3 sm:ml-8">
                            <div className="flex items-center gap-2">
                              <span className="relative flex h-2.5 w-2.5">
                                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-amber-400 opacity-75" />
                                <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-amber-500" />
                              </span>
                              <span className="text-sm font-medium text-amber-600 dark:text-amber-400">
                                Waiting for approval
                              </span>
                            </div>
                            {typeof step.config_json?.prompt === "string" && step.config_json.prompt && (
                              <p className="text-sm text-muted-foreground">
                                {step.config_json.prompt}
                              </p>
                            )}
                            <div className="flex gap-2">
                              <Button
                                size="sm"
                                className="gap-1.5 bg-emerald-600 text-white hover:bg-emerald-700"
                                disabled={approvingSteps.has(step.id)}
                                onClick={() => handleGateResponse(step.id, true)}
                              >
                                {approvingSteps.has(step.id) ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <ShieldCheck className="h-3.5 w-3.5" />
                                )}
                                Approve
                              </Button>
                              <Button
                                size="sm"
                                variant="outline"
                                className="gap-1.5 border-red-500/30 text-red-600 hover:bg-red-500/10 hover:text-red-700"
                                disabled={approvingSteps.has(step.id)}
                                onClick={() => handleGateResponse(step.id, false)}
                              >
                                {approvingSteps.has(step.id) ? (
                                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                ) : (
                                  <ShieldX className="h-3.5 w-3.5" />
                                )}
                                Reject
                              </Button>
                            </div>
                          </div>
                        )}
                      </div>
                    ))}
                </div>
              )}
            </CardContent>
          </Card>
        )}

        {/* Artifacts */}
        {artifacts.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <FileArchive className="h-4 w-4" />
                Artifacts
                <Badge variant="pending" className="ml-1 text-xs">
                  {artifacts.length}
                </Badge>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2">
                {artifacts.map((a) => (
                  <div
                    key={a.id}
                    className="flex flex-col gap-2 rounded-lg border px-3 py-2.5 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-4"
                  >
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-3">
                      <code className="truncate font-medium">{a.relative_path}</code>
                      <span className="text-xs text-muted-foreground">
                        {a.size_bytes < 1024
                          ? `${a.size_bytes} B`
                          : a.size_bytes < 1048576
                            ? `${(a.size_bytes / 1024).toFixed(1)} KB`
                            : `${(a.size_bytes / 1048576).toFixed(1)} MB`}
                      </span>
                      <code className="hidden text-xs text-muted-foreground lg:inline">
                        sha256:{a.checksum_sha256.slice(0, 12)}…
                      </code>
                    </div>
                    <div className="flex items-center gap-1">
                      <button
                        onClick={() => artifactsApi.download(a.id)}
                        className="inline-flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground transition-colors"
                        title="Download"
                      >
                        <Download className="h-3.5 w-3.5" />
                      </button>
                      {canManageArtifacts && (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 text-destructive"
                          onClick={async () => {
                            try {
                              await artifactsApi.delete(a.id);
                              setArtifacts((prev) => prev.filter((x) => x.id !== a.id));
                              toast.success("Artifact deleted");
                            } catch {
                              toast.error("Failed to delete artifact");
                            }
                          }}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Log Viewer */}
        <BuildLogViewer
          lines={
            logLines.length > 0
              ? logLines
              : [
                  {
                    text: `Build #${build.number} — ${build.status}`,
                    stream: "stdout",
                  },
                  {
                    text: `Branch: ${build.branch || "N/A"}`,
                    stream: "stdout",
                  },
                  {
                    text: `Commit: ${build.commit_sha || "N/A"}`,
                    stream: "stdout",
                  },
                  { text: "", stream: "stdout" },
                  ...(isRunning
                    ? [
                        {
                          text: "Connecting to live log stream…",
                          stream: "stdout" as const,
                        },
                      ]
                    : [
                        {
                          text: "Build has finished.",
                          stream: "stdout" as const,
                        },
                        { text: "", stream: "stdout" as const },
                        {
                          text: `Final status: ${build.status}`,
                          stream:
                            build.status === "failed"
                              ? ("stderr" as const)
                              : ("stdout" as const),
                        },
                      ]),
                ]
          }
        />
      </div>
    </AppLayout>
  );
}
