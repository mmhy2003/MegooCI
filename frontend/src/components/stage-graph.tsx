"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { CheckCircle2, XCircle, Loader2, Circle, ArrowRight } from "lucide-react";

export type StageStatus = "pending" | "running" | "success" | "failed" | "cancelled";

export interface Stage {
  id: string;
  name: string;
  status: StageStatus;
}

interface StageGraphProps {
  stages: Stage[];
  selectedStageId?: string | null;
  onSelectStage?: (stageId: string) => void;
}

const statusConfig: Record<
  StageStatus,
  { color: string; bg: string; border: string; icon: React.ElementType }
> = {
  pending: {
    color: "text-gray-500",
    bg: "bg-gray-500/10",
    border: "border-gray-300 dark:border-gray-600",
    icon: Circle,
  },
  running: {
    color: "text-blue-600 dark:text-blue-400",
    bg: "bg-blue-500/10",
    border: "border-blue-400",
    icon: Loader2,
  },
  success: {
    color: "text-emerald-600 dark:text-emerald-400",
    bg: "bg-emerald-500/10",
    border: "border-emerald-400",
    icon: CheckCircle2,
  },
  failed: {
    color: "text-red-600 dark:text-red-400",
    bg: "bg-red-500/10",
    border: "border-red-400",
    icon: XCircle,
  },
  cancelled: {
    color: "text-yellow-600 dark:text-yellow-400",
    bg: "bg-yellow-500/10",
    border: "border-yellow-400",
    icon: Circle,
  },
};

export function StageGraph({
  stages,
  selectedStageId,
  onSelectStage,
}: StageGraphProps) {
  if (stages.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted-foreground">
        No stages defined
      </p>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {stages.map((stage, idx) => {
        const config = statusConfig[stage.status];
        const Icon = config.icon;
        const isSelected = selectedStageId === stage.id;

        return (
          <React.Fragment key={stage.id}>
            {idx > 0 && (
              <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground/40" />
            )}
            <button
              onClick={() => onSelectStage?.(stage.id)}
              className={cn(
                "flex items-center gap-2 rounded-lg border-2 px-3 py-2 text-xs font-medium transition-all sm:px-4 sm:py-2.5 sm:text-sm",
                config.bg,
                config.border,
                isSelected
                  ? "ring-2 ring-primary ring-offset-2 ring-offset-background"
                  : "hover:shadow-md",
              )}
            >
              <Icon
                className={cn(
                  "h-4 w-4 shrink-0",
                  config.color,
                  stage.status === "running" && "animate-spin",
                )}
              />
              <span className={cn("break-all", config.color)}>
                {stage.name}
              </span>
            </button>
          </React.Fragment>
        );
      })}
    </div>
  );
}
