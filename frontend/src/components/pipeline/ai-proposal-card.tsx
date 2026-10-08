"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import type { AiDiffHunk, AiDiffLine, AiProposal } from "@/lib/api";
import {
  AlertTriangle,
  ArrowDownToLine,
  Check,
  Copy,
  Undo2,
} from "lucide-react";
import { Button } from "@/components/ui/button";

/** Longer diffs are cut. Apply and Copy still use the whole proposal. */
const MAX_DIFF_LINES = 400;

export type ProposalDecision = "applied" | "discarded";

interface AiProposalCardProps {
  proposal: AiProposal;
  /** Undefined until the user applies or discards the proposal. */
  decision?: ProposalDecision;
  /** The editor no longer holds the YAML this proposal was made from. */
  stale: boolean;
  /** The assistant stopped at a limit, so the proposal may be incomplete. */
  limitReached: boolean;
  canUndo: boolean;
  /** Undefined when the editor cannot be changed (not in edit mode). */
  onApply?: () => void;
  onDiscard: () => void;
  onUndo: () => void;
}

function hunkTitle(hunk: AiDiffHunk): string {
  const numbers = hunk.lines
    .map((line) => line.new)
    .filter((n): n is number => n !== null);
  if (numbers.length === 0) return `removed at line ${hunk.old_start}`;
  const first = numbers[0];
  const last = numbers[numbers.length - 1];
  return first === last ? `line ${first}` : `lines ${first}–${last}`;
}

function cutHunks(hunks: AiDiffHunk[]): { shown: AiDiffHunk[]; hidden: number } {
  const shown: AiDiffHunk[] = [];
  let room = MAX_DIFF_LINES;
  let hidden = 0;
  for (const hunk of hunks) {
    if (room === 0) {
      hidden += hunk.lines.length;
    } else if (hunk.lines.length <= room) {
      shown.push(hunk);
      room -= hunk.lines.length;
    } else {
      shown.push({ ...hunk, lines: hunk.lines.slice(0, room) });
      hidden += hunk.lines.length - room;
      room = 0;
    }
  }
  return { shown, hidden };
}

function Counts({ added, removed }: { added: number; removed: number }) {
  return (
    <span className="font-mono">
      <span className="text-success">+{added}</span>{" "}
      <span className="text-destructive">−{removed}</span>
    </span>
  );
}

function DiffRow({ line }: { line: AiDiffLine }) {
  const isAdd = line.kind === "add";
  const isRemove = line.kind === "remove";
  return (
    <div
      className={cn(
        "flex",
        isAdd && "bg-success/10",
        isRemove && "bg-destructive/10",
      )}
    >
      <span className="w-10 shrink-0 select-none pr-2 text-right text-muted-foreground/70">
        {isRemove ? line.old : line.new}
      </span>
      <span
        className={cn(
          "w-4 shrink-0 select-none",
          isAdd && "text-success",
          isRemove && "text-destructive",
        )}
      >
        {isAdd ? "+" : isRemove ? "−" : ""}
      </span>
      <span className="whitespace-pre pr-3">{line.text}</span>
    </div>
  );
}

/**
 * The assistant's proposed change, shown as a diff the user applies or
 * discards. Nothing reaches the editor until Apply.
 */
export function AiProposalCard({
  proposal,
  decision,
  stale,
  limitReached,
  canUndo,
  onApply,
  onDiscard,
  onUndo,
}: AiProposalCardProps) {
  const [copied, setCopied] = React.useState(false);
  const { shown, hidden } = React.useMemo(
    () => cutHunks(proposal.hunks),
    [proposal.hunks],
  );

  if (decision === "applied") {
    return (
      <div className="my-2 flex items-center justify-between gap-2 rounded-md border bg-muted/30 px-3 py-1.5 text-xs">
        <span className="flex items-center gap-1.5">
          <Check className="h-3 w-3 text-success" />
          Applied to editor ·{" "}
          <Counts added={proposal.added} removed={proposal.removed} />
        </span>
        {canUndo && (
          <button
            type="button"
            onClick={onUndo}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            <Undo2 className="h-3 w-3" />
            Undo
          </button>
        )}
      </div>
    );
  }

  if (decision === "discarded") {
    return (
      <div className="my-2 rounded-md border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground">
        Discarded ·{" "}
        <Counts added={proposal.added} removed={proposal.removed} />
      </div>
    );
  }

  const problems = proposal.problems;

  return (
    <div className="my-2 overflow-hidden rounded-md border bg-muted/30">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b bg-muted/50 px-3 py-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          Proposed change
        </span>
        <div className="flex items-center gap-2.5 text-xs">
          <Counts added={proposal.added} removed={proposal.removed} />
          {problems.length === 0 ? (
            <span className="flex items-center gap-1 text-success">
              <Check className="h-3 w-3" />
              valid
            </span>
          ) : (
            <span className="flex items-center gap-1 text-warning">
              <AlertTriangle className="h-3 w-3" />
              {problems.length} {problems.length === 1 ? "problem" : "problems"}
            </span>
          )}
          <button
            type="button"
            onClick={() => {
              navigator.clipboard.writeText(proposal.yaml);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
            {copied ? "Copied" : "Copy YAML"}
          </button>
        </div>
      </div>

      {(limitReached || stale || problems.length > 0) && (
        <div className="space-y-1 border-b px-3 py-2 text-xs">
          {limitReached && (
            <p className="text-warning">
              The assistant stopped early, so this change may be incomplete.
            </p>
          )}
          {stale && (
            <p className="text-warning">
              The editor changed since this was proposed. Applying replaces what
              is in the editor now.
            </p>
          )}
          {problems.map((problem, i) => (
            <p key={i} className="text-muted-foreground">
              {problem.line !== null && (
                <span className="font-mono">Line {problem.line}: </span>
              )}
              {problem.message}
            </p>
          ))}
        </div>
      )}

      <div className="max-h-80 overflow-auto font-mono text-xs leading-relaxed">
        <div className="w-max min-w-full">
          {shown.map((hunk, i) => (
            <React.Fragment key={i}>
              <div className="border-b bg-muted/40 px-3 py-0.5 text-[10px] text-muted-foreground">
                {hunkTitle(hunk)}
              </div>
              {hunk.lines.map((line, j) => (
                <DiffRow key={j} line={line} />
              ))}
            </React.Fragment>
          ))}
          {hidden > 0 && (
            <div className="border-t px-3 py-1 font-sans text-[11px] text-muted-foreground">
              {hidden} more {hidden === 1 ? "line is" : "lines are"} not shown.
              Copy YAML to see the whole result.
            </div>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between gap-2 border-t px-3 py-2">
        <Button type="button" variant="ghost" size="sm" onClick={onDiscard}>
          Discard
        </Button>
        {onApply ? (
          <Button type="button" size="sm" onClick={onApply}>
            <ArrowDownToLine className="mr-1.5 h-3.5 w-3.5" />
            {stale ? "Apply anyway" : "Apply to editor"}
          </Button>
        ) : (
          <span className="text-xs text-muted-foreground">
            Edit the pipeline to apply this change.
          </span>
        )}
      </div>
    </div>
  );
}
