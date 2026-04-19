"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { Hammer } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { buildsApi, type Build, type BuildStatus } from "@/lib/api";
import { Card, CardContent } from "@/components/ui/card";
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
  return `${mins}m ${secs % 60}s`;
}

const STATUS_TABS: { label: string; value: BuildStatus | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Running", value: "running" },
  { label: "Success", value: "success" },
  { label: "Failed", value: "failed" },
];

export default function BuildsPage() {
  const router = useRouter();
  const [allBuilds, setAllBuilds] = React.useState<Build[]>([]);
  const [statusFilter, setStatusFilter] = React.useState<
    BuildStatus | "all"
  >("all");
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    buildsApi
      .list({ limit: 100 })
      .then(setAllBuilds)
      .catch(() => toast.error("Failed to load builds"))
      .finally(() => setLoading(false));
  }, []);

  const builds =
    statusFilter === "all"
      ? allBuilds
      : allBuilds.filter((b) => b.status === statusFilter);

  return (
    <AppLayout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Builds</h1>
          <p className="text-muted-foreground">
            {allBuilds.length} build{allBuilds.length !== 1 ? "s" : ""} total
          </p>
        </div>

        <div className="flex gap-1 border-b">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => setStatusFilter(tab.value)}
              className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
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
              <div className="space-y-3">
                {Array.from({ length: 8 }).map((_, i) => (
                  <div key={i} className="flex items-center gap-4">
                    <Skeleton className="h-4 w-16" />
                    <Skeleton className="h-4 w-32" />
                    <Skeleton className="h-4 w-20" />
                    <Skeleton className="h-5 w-16 rounded-md" />
                    <Skeleton className="ml-auto h-4 w-20" />
                  </div>
                ))}
              </div>
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
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-3 pr-4 font-medium">Build</th>
                      <th className="pb-3 pr-4 font-medium">Pipeline</th>
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
                          <button
                            className="text-primary hover:underline"
                            onClick={(e) => {
                              e.stopPropagation();
                              router.push(`/pipelines/${build.pipeline_id}`);
                            }}
                          >
                            {build.pipeline_id.slice(0, 8)}…
                          </button>
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
                          {formatDuration(build.started_at, build.finished_at)}
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
      </div>
    </AppLayout>
  );
}
