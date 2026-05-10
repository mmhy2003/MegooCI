"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  GitBranch,
  Hammer,
  CheckCircle2,
  Server,
  FolderKanban,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useAuthStore } from "@/lib/auth";
import {
  agentsApi,
  buildsApi,
  pipelinesApi,
  projectsApi,
  type Build,
  type BuildStatus,
  type Pipeline,
  type Project,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
}

/** Recalculate summary stats from the current build list. */
function calcStats(
  builds: Build[],
  activeAgents: number,
  totalPipelines: number,
) {
  const successCount = builds.filter((b) => b.status === "success").length;
  return {
    totalPipelines,
    totalBuilds: builds.length,
    successRate:
      builds.length > 0
        ? Math.round((successCount / builds.length) * 100)
        : 0,
    activeAgents,
  };
}

interface Stats {
  totalPipelines: number;
  totalBuilds: number;
  successRate: number;
  activeAgents: number;
}

export default function DashboardPage() {
  const { user } = useAuthStore();
  const router = useRouter();
  const [stats, setStats] = React.useState<Stats | null>(null);
  const [recentBuilds, setRecentBuilds] = React.useState<Build[] | null>(null);
  const [pipelineMap, setPipelineMap] = React.useState<
    Record<string, Pipeline>
  >({});
  const [projectMap, setProjectMap] = React.useState<Record<string, Project>>(
    {},
  );
  const [loading, setLoading] = React.useState(true);

  // Stable refs so the WS callback can access current state without
  // triggering re-connection on every render.
  const recentBuildsRef = React.useRef<Build[] | null>(null);
  recentBuildsRef.current = recentBuilds;
  const agentCountRef = React.useRef(0);
  const pipelineCountRef = React.useRef(0);

  React.useEffect(() => {
    async function fetchData() {
      try {
        const [pipelines, builds, agents, projects] = await Promise.all([
          pipelinesApi.list(),
          buildsApi.list({ limit: 10 }),
          agentsApi.list().catch(() => []),
          projectsApi.list({ limit: 100 }),
        ]);
        const activeAgents = agents.filter(
          (a) => a.status === "online" || a.status === "busy",
        ).length;
        agentCountRef.current = activeAgents;
        pipelineCountRef.current = pipelines.length;

        setStats(calcStats(builds, activeAgents, pipelines.length));
        setRecentBuilds(builds);

        const pMap: Record<string, Pipeline> = {};
        for (const p of pipelines) pMap[p.id] = p;
        setPipelineMap(pMap);

        const prjMap: Record<string, Project> = {};
        for (const p of projects) prjMap[p.id] = p;
        setProjectMap(prjMap);
      } catch {
        toast.error("Failed to load dashboard data");
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

  // Live updates via WebSocket —————————————————————————————————————
  useBuildUpdates(
    React.useCallback((update) => {
      setRecentBuilds((prev) => {
        if (!prev) return prev;

        const idx = prev.findIndex((b) => b.id === update.id);
        let next: Build[];

        if (idx !== -1) {
          // Update existing build in-place (status / timestamps changed).
          next = prev.map((b, i) =>
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
        } else {
          // Brand-new build — prepend and keep only the 10 most recent.
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
          next = [newBuild, ...prev].slice(0, 10);
        }

        // Recalculate summary stats.
        setStats(
          calcStats(
            next,
            agentCountRef.current,
            pipelineCountRef.current,
          ),
        );

        return next;
      });
    }, []),
  );

  const statCards = [
    {
      title: "Total Pipelines",
      value: stats?.totalPipelines ?? 0,
      icon: GitBranch,
      color: "text-cyan-600 dark:text-cyan-400",
      bg: "bg-cyan-500/10",
    },
    {
      title: "Total Builds",
      value: stats?.totalBuilds ?? 0,
      icon: Hammer,
      color: "text-amber-600 dark:text-amber-400",
      bg: "bg-amber-500/10",
    },
    {
      title: "Success Rate",
      value: `${stats?.successRate ?? 0}%`,
      icon: CheckCircle2,
      color: "text-emerald-600 dark:text-emerald-400",
      bg: "bg-emerald-500/10",
    },
    {
      title: "Active Agents",
      value: stats?.activeAgents ?? 0,
      icon: Server,
      color: "text-purple-600 dark:text-purple-400",
      bg: "bg-purple-500/10",
    },
  ];

  return (
    <AppLayout>
      <div className="space-y-8">
        {/* Welcome */}
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
              Welcome back{user?.name ? `, ${user.name}` : ""}
            </h1>
            <p className="text-sm text-muted-foreground sm:text-base">
              Here&apos;s what&apos;s happening with your pipelines today.
            </p>
          </div>
        </div>

        {/* Stats */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {statCards.map((card) =>
            loading ? (
              <Card key={card.title}>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <Skeleton className="h-4 w-24" />
                  <Skeleton className="h-8 w-8 rounded-lg" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-8 w-16" />
                </CardContent>
              </Card>
            ) : (
              <Card key={card.title}>
                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                  <CardTitle className="text-sm font-medium text-muted-foreground">
                    {card.title}
                  </CardTitle>
                  <div className={`rounded-lg p-2 ${card.bg}`}>
                    <card.icon className={`h-4 w-4 ${card.color}`} />
                  </div>
                </CardHeader>
                <CardContent>
                  <div className="text-2xl font-bold">{card.value}</div>
                </CardContent>
              </Card>
            ),
          )}
        </div>

        {/* Recent Builds */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
            <CardTitle className="text-base sm:text-lg">Recent Builds</CardTitle>
            <Button variant="ghost" size="sm" asChild>
              <Link href="/builds">View all</Link>
            </Button>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-4">
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-4 w-24" />
                    <Skeleton className="h-4 w-16" />
                    <Skeleton className="h-4 w-20" />
                    <Skeleton className="h-5 w-16 rounded-md" />
                    <Skeleton className="ml-auto h-4 w-24" />
                  </div>
                ))}
              </div>
            ) : !recentBuilds?.length ? (
              <div className="py-12 text-center">
                <Hammer className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
                <p className="text-sm text-muted-foreground">
                  No builds yet. Trigger your first build!
                </p>
              </div>
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="w-full min-w-[540px] text-sm">
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
                      <th className="pb-3 font-medium text-right">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentBuilds.map((build) => {
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
            )}
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
