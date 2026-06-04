"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { Hammer, FolderKanban } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import {
  buildsApi,
  pipelinesApi,
  projectsApi,
  type Build,
  type BuildStatus,
  type Pipeline,
  type Project,
} from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useBuildUpdates } from "@/hooks/use-build-updates";

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

const STATUS_TABS: { label: string; value: BuildStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Running", value: "running" },
  { label: "Success", value: "success" },
  { label: "Failed", value: "failed" },
];

function BuildCard({
  build,
  pipeline,
  project,
}: {
  build: Build;
  pipeline?: Pipeline;
  project?: Project;
}) {
  const router = useRouter();
  return (
    <div
      onClick={() => router.push(`/builds/${build.id}`)}
      className="cursor-pointer px-2 py-3 transition-colors hover:bg-muted/50"
    >
      {/* Header: number · status · time */}
      <div className="flex items-center gap-2">
        <span className="font-medium">#{build.number}</span>
        <Badge variant={statusVariant(build.status)}>{build.status}</Badge>
        <span className="ml-auto text-xs text-muted-foreground">
          {formatDistanceToNow(new Date(build.created_at), { addSuffix: true })}
        </span>
      </div>

      {/* Pipeline */}
      <div className="mt-2">
        <button
          className="text-sm text-primary hover:underline"
          onClick={(e) => {
            e.stopPropagation();
            router.push(`/pipelines/${build.pipeline_id}`);
          }}
        >
          {pipeline?.name || build.pipeline_id.slice(0, 8) + "…"}
        </button>
      </div>

      {/* Project */}
      <div className="mt-1.5 text-sm">
        {project ? (
          <button
            className="flex items-center gap-1.5 text-muted-foreground transition-colors hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              router.push(`/projects/${project.id}`);
            }}
          >
            <FolderKanban className="h-3.5 w-3.5" />
            {project.name}
          </button>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </div>

      {/* Meta: branch · duration · trigger */}
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <code className="rounded bg-muted px-1.5 py-0.5">
          {build.branch || "—"}
        </code>
        <span>·</span>
        <span>{formatDuration(build.started_at, build.finished_at)}</span>
        <span>·</span>
        <span>{build.trigger_type}</span>
      </div>
    </div>
  );
}

export default function BuildsPage() {
  const router = useRouter();
  const [allBuilds, setAllBuilds] = React.useState<Build[]>([]);
  const [pipelineMap, setPipelineMap] = React.useState<
    Record<string, Pipeline>
  >({});
  const [projectMap, setProjectMap] = React.useState<Record<string, Project>>(
    {},
  );
  const [statusFilter, setStatusFilter] = React.useState<BuildStatus | "all">(
    "all",
  );
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    async function load() {
      try {
        const [builds, pipelines, projects] = await Promise.all([
          buildsApi.list({ limit: 100 }),
          pipelinesApi.list({ limit: 100 }),
          projectsApi.list({ limit: 100 }),
        ]);
        setAllBuilds(builds);
        const pMap: Record<string, Pipeline> = {};
        for (const p of pipelines) pMap[p.id] = p;
        setPipelineMap(pMap);
        const prjMap: Record<string, Project> = {};
        for (const p of projects) prjMap[p.id] = p;
        setProjectMap(prjMap);
      } catch {
        toast.error("Failed to load builds");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  // Live updates via WebSocket ————————————————————————————————————
  useBuildUpdates(
    React.useCallback((update) => {
      setAllBuilds((prev) => {
        const idx = prev.findIndex((b) => b.id === update.id);
        if (idx !== -1) {
          // Update status / timestamps for an existing build.
          return prev.map((b, i) =>
            i === idx
              ? {
                  ...b,
                  status: update.status as BuildStatus,
                  started_at: update.started_at,
                  finished_at: update.finished_at,
                  updated_at: update.updated_at ?? b.updated_at,
                }
              : b,
          );
        }
        // New build — prepend so it appears at the top of the list.
        const newBuild: Build = {
          id: update.id,
          pipeline_id: update.pipeline_id,
          number: update.number,
          branch: update.branch,
          commit_sha: update.commit_sha,
          status: update.status as BuildStatus,
          trigger_type: update.trigger_type,
          started_at: update.started_at,
          finished_at: update.finished_at,
          created_at: update.created_at ?? new Date().toISOString(),
          updated_at: update.updated_at ?? new Date().toISOString(),
          triggered_by: update.triggered_by,
          params_json: null,
        };
        return [newBuild, ...prev];
      });
    }, []),
  );

  const builds =
    statusFilter === "all"
      ? allBuilds
      : allBuilds.filter((b) => b.status === statusFilter);

  return (
    <AppLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            Builds
          </h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            {allBuilds.length} build{allBuilds.length !== 1 ? "s" : ""} total
          </p>
        </div>

        <div className="-mx-4 flex gap-1 overflow-x-auto border-b px-4 sm:mx-0 sm:px-0">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => setStatusFilter(tab.value)}
              className={`shrink-0 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                statusFilter === tab.value
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <Card>
          <CardContent className="pt-6">
            {loading ? (
              <>
                {/* Desktop: row skeletons */}
                <div className="hidden space-y-3 sm:block">
                  {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4">
                      <Skeleton className="h-4 w-16" />
                      <Skeleton className="h-4 w-32" />
                      <Skeleton className="h-4 w-20" />
                      <Skeleton className="h-4 w-20" />
                      <Skeleton className="h-5 w-16 rounded-md" />
                      <Skeleton className="ml-auto h-4 w-20" />
                    </div>
                  ))}
                </div>
                {/* Mobile: card skeletons */}
                <div className="space-y-4 sm:hidden">
                  {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="space-y-2">
                      <div className="flex items-center gap-2">
                        <Skeleton className="h-4 w-12" />
                        <Skeleton className="h-5 w-16 rounded-md" />
                        <Skeleton className="ml-auto h-3 w-16" />
                      </div>
                      <Skeleton className="h-4 w-32" />
                      <Skeleton className="h-4 w-24" />
                      <Skeleton className="h-3 w-40" />
                    </div>
                  ))}
                </div>
              </>
            ) : builds.length === 0 ? (
              <div className="py-16 text-center">
                <Hammer className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">
                  No builds found
                  {statusFilter !== "all"
                    ? ` with status "${statusFilter}"`
                    : ""}
                  .
                </p>
              </div>
            ) : (
              <>
                <div className="-mx-2 hidden overflow-x-auto px-2 sm:block">
                  <table className="w-full min-w-[560px] text-sm">
                    <thead>
                      <tr className="border-b text-left text-muted-foreground">
                        <th className="pb-3 pr-4 font-medium">Build</th>
                        <th className="hidden pb-3 pr-4 font-medium sm:table-cell">
                          Pipeline
                        </th>
                        <th className="hidden pb-3 pr-4 font-medium lg:table-cell">
                          Project
                        </th>
                        <th className="hidden pb-3 pr-4 font-medium md:table-cell">
                          Branch
                        </th>
                        <th className="pb-3 pr-4 font-medium">Status</th>
                        <th className="hidden pb-3 pr-4 font-medium sm:table-cell">
                          Duration
                        </th>
                        <th className="hidden pb-3 pr-4 font-medium xl:table-cell">
                          Trigger
                        </th>
                        <th className="pb-3 font-medium text-right">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {builds.map((build) => {
                        const pl = pipelineMap[build.pipeline_id];
                        const prj = pl ? projectMap[pl.project_id] : undefined;
                        return (
                          <tr
                            key={build.id}
                            className="border-b last:border-0 hover:bg-muted/50 cursor-pointer transition-colors"
                            onClick={() => router.push(`/builds/${build.id}`)}
                          >
                            <td className="py-3 pr-4 font-medium">
                              #{build.number}
                            </td>
                            <td className="hidden py-3 pr-4 sm:table-cell">
                              <button
                                className="text-primary hover:underline"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  router.push(
                                    `/pipelines/${build.pipeline_id}`,
                                  );
                                }}
                              >
                                {pl?.name ||
                                  build.pipeline_id.slice(0, 8) + "…"}
                              </button>
                            </td>
                            <td className="hidden py-3 pr-4 lg:table-cell">
                              {prj ? (
                                <button
                                  className="flex items-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors"
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    router.push(`/projects/${prj.id}`);
                                  }}
                                >
                                  <FolderKanban className="h-3.5 w-3.5" />
                                  {prj.name}
                                </button>
                              ) : (
                                <span className="text-muted-foreground">—</span>
                              )}
                            </td>
                            <td className="hidden py-3 pr-4 md:table-cell">
                              <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
                                {build.branch || "—"}
                              </code>
                            </td>
                            <td className="py-3 pr-4">
                              <Badge variant={statusVariant(build.status)}>
                                {build.status}
                              </Badge>
                            </td>
                            <td className="hidden py-3 pr-4 text-muted-foreground sm:table-cell">
                              {formatDuration(
                                build.started_at,
                                build.finished_at,
                              )}
                            </td>
                            <td className="hidden py-3 pr-4 text-muted-foreground xl:table-cell">
                              {build.trigger_type}
                            </td>
                            <td className="py-3 text-right text-muted-foreground">
                              {formatDistanceToNow(
                                new Date(build.created_at),
                                { addSuffix: true },
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div className="-mx-2 divide-y sm:hidden">
                  {builds.map((build) => {
                    const pl = pipelineMap[build.pipeline_id];
                    const prj = pl ? projectMap[pl.project_id] : undefined;
                    return (
                      <BuildCard
                        key={build.id}
                        build={build}
                        pipeline={pl}
                        project={prj}
                      />
                    );
                  })}
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
