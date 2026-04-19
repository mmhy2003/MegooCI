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
  Plus,
  Play,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useAuthStore } from "@/lib/auth";
import {
  agentsApi,
  buildsApi,
  pipelinesApi,
  type Build,
  type BuildStatus,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
  const rem = secs % 60;
  return `${mins}m ${rem}s`;
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
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    async function fetchData() {
      try {
        const [pipelines, builds, agents] = await Promise.all([
          pipelinesApi.list(),
          buildsApi.list({ limit: 10 }),
          agentsApi.list().catch(() => []),
        ]);
        const successCount = builds.filter(
          (b) => b.status === "success",
        ).length;
        const activeAgents = agents.filter(
          (a) => a.status === "online" || a.status === "busy",
        ).length;
        setStats({
          totalPipelines: pipelines.length,
          totalBuilds: builds.length,
          successRate:
            builds.length > 0
              ? Math.round((successCount / builds.length) * 100)
              : 0,
          activeAgents,
        });
        setRecentBuilds(builds);
      } catch {
        toast.error("Failed to load dashboard data");
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, []);

  const statCards = [
    {
      title: "Total Pipelines",
      value: stats?.totalPipelines ?? 0,
      icon: GitBranch,
      color: "text-blue-600 dark:text-blue-400",
      bg: "bg-blue-500/10",
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
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">
              Welcome back{user?.name ? `, ${user.name}` : ""}
            </h1>
            <p className="text-muted-foreground">
              Here&apos;s what&apos;s happening with your pipelines today.
            </p>
          </div>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => router.push("/pipelines/new")}
            >
              <Plus className="mr-1.5 h-4 w-4" />
              New Pipeline
            </Button>
            <Button onClick={() => router.push("/pipelines")}>
              <Play className="mr-1.5 h-4 w-4" />
              Trigger Build
            </Button>
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
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Recent Builds</CardTitle>
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
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-3 pr-4 font-medium">Pipeline</th>
                      <th className="pb-3 pr-4 font-medium">Build</th>
                      <th className="pb-3 pr-4 font-medium">Branch</th>
                      <th className="pb-3 pr-4 font-medium">Status</th>
                      <th className="pb-3 pr-4 font-medium">Duration</th>
                      <th className="pb-3 font-medium text-right">Time</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentBuilds.map((build) => (
                      <tr
                        key={build.id}
                        className="border-b last:border-0 hover:bg-muted/50 cursor-pointer transition-colors"
                        onClick={() => router.push(`/builds/${build.id}`)}
                      >
                        <td className="py-3 pr-4 font-medium">
                          {build.pipeline_id.slice(0, 8)}…
                        </td>
                        <td className="py-3 pr-4">#{build.number}</td>
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
                          {formatDuration(build.started_at, build.finished_at)}
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
      </div>
    </AppLayout>
  );
}
