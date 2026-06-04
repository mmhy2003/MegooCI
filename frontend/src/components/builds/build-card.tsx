"use client";

import { useRouter } from "next/navigation";
import { formatDistanceToNow } from "date-fns";
import { FolderKanban } from "lucide-react";
import { type Build, type Pipeline, type Project } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { statusVariant, formatDuration } from "@/lib/builds";

export function BuildCard({
  build,
  pipeline,
  project,
  showTrigger = true,
}: {
  build: Build;
  pipeline?: Pipeline;
  project?: Project;
  showTrigger?: boolean;
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
        {showTrigger && (
          <>
            <span>·</span>
            <span>{build.trigger_type}</span>
          </>
        )}
      </div>
    </div>
  );
}
