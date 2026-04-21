"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  ArrowLeft,
  RotateCw,
  XCircle,
  GitBranch,
  Clock,
  Hash,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import {
  buildsApi,
  type BuildDetail,
  type BuildStatus,
  type BuildStage,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { StageGraph, type Stage, type StageStatus } from "@/components/stage-graph";
import { BuildLogViewer, type LogLine } from "@/components/build-log-viewer";
import { useWebSocket } from "@/hooks/use-websocket";
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

  const [build, setBuild] = React.useState<BuildDetail | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [selectedStageId, setSelectedStageId] = React.useState<string>("");

  const isRunning = build?.status === "running" || build?.status === "queued";

  const wsUrl = React.useMemo(() => {
    if (!isRunning || typeof window === "undefined") return null;
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "";
    if (apiBase) {
      const url = new URL(apiBase);
      const wsProto = url.protocol === "https:" ? "wss:" : "ws:";
      return `${wsProto}//${url.host}/api/v1/ws/builds/${id}/logs`;
    }
    const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${wsProto}//${window.location.host}/api/v1/ws/builds/${id}/logs`;
  }, [isRunning, id]);
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
      })
      .catch(() => toast.error("Failed to load build"))
      .finally(() => setLoading(false));
  }, [id]);

  React.useEffect(() => {
    const newLines = messages.map(
      (msg): LogLine => {
        try {
          const parsed = JSON.parse(msg);
          return {
            text: parsed.text || parsed.content || msg,
            timestamp: parsed.timestamp,
            stream: parsed.stream || "stdout",
          };
        } catch {
          return { text: msg, stream: "stdout" };
        }
      },
    );
    setLogLines(newLines);
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
              <span className="flex items-center gap-1">
                <Hash className="h-3.5 w-3.5" />
                {build.pipeline_id.slice(0, 8)}…
              </span>
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
              <Button
                variant="outline"
                size="sm"
                onClick={handleRetry}
                className="flex-1 sm:flex-none"
              >
                <RotateCw className="mr-1.5 h-4 w-4" />
                Re-run
              </Button>
              {isRunning && (
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
                        className="flex flex-col gap-2 rounded-lg border px-3 py-3 text-sm sm:flex-row sm:items-center sm:justify-between sm:px-4"
                      >
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
                    ))}
                </div>
              )}
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
