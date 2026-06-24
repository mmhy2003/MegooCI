"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { Plus, GitBranch, ExternalLink, FolderKanban } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { pipelinesApi, projectsApi, type Pipeline, type Project } from "@/lib/api";
import { usePermission } from "@/hooks/use-permission";
import { useAuthStore } from "@/lib/auth";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";

export default function PipelinesPage() {
  const router = useRouter();
  const canManage = usePermission("pipelines.manage");
  const { user } = useAuthStore();
  const [pipelines, setPipelines] = React.useState<Pipeline[]>([]);
  const [projectMap, setProjectMap] = React.useState<Record<string, Project>>({});
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    async function load() {
      try {
        const [pips, projects] = await Promise.all([
          pipelinesApi.list(),
          projectsApi.list({ limit: 100 }),
        ]);
        setPipelines(pips);
        const pMap: Record<string, Project> = {};
        for (const p of projects) pMap[p.id] = p;
        setProjectMap(pMap);
      } catch {
        toast.error("Failed to load pipelines");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  return (
    <AppLayout>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
              Pipelines
            </h1>
            <p className="text-sm text-muted-foreground sm:text-base">
              {pipelines.length} pipeline{pipelines.length !== 1 ? "s" : ""}{" "}
              configured
            </p>
          </div>
          {canManage && (
            <Button
              onClick={() => router.push("/pipelines/new")}
              className="w-full sm:w-auto"
            >
              <Plus className="mr-1.5 h-4 w-4" />
              New Pipeline
            </Button>
          )}
        </div>

        {/* Loading */}
        {loading ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={i}>
                <CardHeader>
                  <Skeleton className="h-5 w-32" />
                  <Skeleton className="h-4 w-48" />
                </CardHeader>
                <CardContent className="space-y-2">
                  <Skeleton className="h-4 w-40" />
                  <Skeleton className="h-4 w-24" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : pipelines.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-16">
              <div className="mb-4 rounded-full bg-muted p-4">
                <GitBranch className="h-8 w-8 text-muted-foreground" />
              </div>
              <h3 className="mb-1 text-lg font-semibold">No pipelines yet</h3>
              <p className="mb-6 max-w-sm text-center text-sm text-muted-foreground">
                {user?.is_admin
                  ? "Create your first pipeline to start automating your builds and deployments."
                  : "No projects assigned yet — ask an admin to grant you access."}
              </p>
              {canManage && (
                <Button onClick={() => router.push("/pipelines/new")}>
                  <Plus className="mr-1.5 h-4 w-4" />
                  Create Pipeline
                </Button>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {pipelines.map((pipeline) => (
              <Link key={pipeline.id} href={`/pipelines/${pipeline.id}`}>
                <Card className="h-full transition-shadow hover:shadow-lg cursor-pointer">
                  <CardHeader>
                    <div className="flex items-start justify-between">
                      <CardTitle className="text-base">
                        {pipeline.name}
                      </CardTitle>
                      <Badge variant="secondary" className="ml-2 shrink-0">
                        YAML
                      </Badge>
                    </div>
                    {pipeline.source_repo_url && (
                      <CardDescription className="line-clamp-1">
                        {pipeline.source_repo_url}
                      </CardDescription>
                    )}
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm text-muted-foreground">
                    {projectMap[pipeline.project_id] && (
                      <div className="flex items-center gap-1.5">
                        <FolderKanban className="h-3.5 w-3.5" />
                        <span className="truncate">{projectMap[pipeline.project_id].name}</span>
                      </div>
                    )}
                    <div className="flex items-center gap-1.5">
                      <GitBranch className="h-3.5 w-3.5" />
                      <span>{pipeline.default_branch}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                        <ExternalLink className="h-3.5 w-3.5" />
                        <span className={pipeline.enabled ? "text-emerald-500" : "text-muted-foreground"}>
                          {pipeline.enabled ? "Active" : "Disabled"}
                        </span>
                      </div>
                    <p className="text-xs">
                      Created{" "}
                      {formatDistanceToNow(new Date(pipeline.created_at), {
                        addSuffix: true,
                      })}
                    </p>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </div>
    </AppLayout>
  );
}
