"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import {
  aiAssistantApi,
  type AiAssistantStep,
  type AiChatMessage,
  type AiProposal,
} from "@/lib/api";
import { toast } from "sonner";
import {
  Sparkles,
  Send,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  User,
  Bot,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  AiProposalCard,
  type ProposalDecision,
} from "@/components/pipeline/ai-proposal-card";

/**
 * Longer than the server needs for its tool loop (120s) plus one retry
 * without tools, so the server's own answer normally arrives first.
 */
const REQUEST_TIMEOUT_MS = 250_000;

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  /** Tools the assistant used for this reply. */
  steps?: AiAssistantStep[];
  proposal?: AiProposal | null;
  limitReached?: boolean;
  /** Editor content when the request was sent; the proposal is a diff against it. */
  baseYaml?: string;
  decision?: ProposalDecision;
}

/** The last applied proposal, while it can still be undone. */
interface UndoState {
  messageId: string;
  before: string;
  after: string;
}

interface AiAssistantPanelProps {
  className?: string;
  currentYaml: string;
  onApplyYaml?: (yaml: string) => void;
  projectId?: string | null;
  pipelineId?: string | null;
  repoUrl?: string | null;
  branch?: string | null;
  /** Called when the user clicks the close button in the header. */
  onClose?: () => void;
}

const QUICK_PROMPTS = [
  "Build and push a Docker image",
  "Deploy via SSH after approval",
  "Full CI/CD with test, build, deploy",
  "Add a webhook gate before deploy",
  "Clone repo, install, test, and push",
];

/**
 * A past message as the model should see it. The reply of a proposal does not
 * contain its YAML, and the editor only has it once applied, so the outcome
 * is spelled out, with the YAML of the proposal that is still open.
 */
function historyContent(msg: Message, isOpenProposal: boolean): string {
  if (!msg.proposal) return msg.content;
  if (msg.decision === "applied") {
    return `${msg.content}\n\n[The user applied this proposed change to the editor.]`;
  }
  if (msg.decision === "discarded") {
    return `${msg.content}\n\n[The user discarded this proposed change.]`;
  }
  if (!isOpenProposal) {
    return `${msg.content}\n\n[This change was proposed but not applied.]`;
  }
  return (
    `${msg.content}\n\n[This change was proposed but not applied yet: the editor ` +
    `still holds the earlier YAML. The proposed YAML was:]\n` +
    `\`\`\`yaml\n${msg.proposal.yaml}\`\`\``
  );
}

function StepRows({ steps }: { steps: AiAssistantStep[] }) {
  return (
    <ul className="space-y-0.5 text-xs text-muted-foreground">
      {steps.map((step, i) => (
        <li key={i} className="flex items-start gap-1.5">
          {step.ok ? (
            <Check className="mt-0.5 h-3 w-3 shrink-0 text-success" />
          ) : (
            <X className="mt-0.5 h-3 w-3 shrink-0 text-destructive" />
          )}
          <span className="min-w-0 break-words">{step.label}</span>
        </li>
      ))}
    </ul>
  );
}

/** The steps of a finished reply: one line that expands to the list. */
function StepSummary({ steps }: { steps: AiAssistantStep[] }) {
  const [open, setOpen] = React.useState(false);
  if (steps.length === 0) return null;
  return (
    <div className="mb-1.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex items-center gap-1 rounded text-xs text-muted-foreground hover:text-foreground transition-colors"
      >
        {open ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        {steps.length} {steps.length === 1 ? "step" : "steps"}
      </button>
      {open && (
        <div className="mt-1 pl-4">
          <StepRows steps={steps} />
        </div>
      )}
    </div>
  );
}

/** Reply text, with ``` fenced blocks shown as code. */
function ReplyText({ text }: { text: string }) {
  const parts = text.split(/```[\w-]*\n?([\s\S]*?)```/g);
  return (
    <div className="text-sm leading-relaxed">
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <pre
            key={i}
            className="my-2 overflow-x-auto rounded-md border bg-muted/30 p-3 text-xs leading-relaxed"
          >
            <code>{part.replace(/\n$/, "")}</code>
          </pre>
        ) : part.trim() ? (
          <div key={i} className="whitespace-pre-wrap">
            {part.trim()}
          </div>
        ) : null,
      )}
    </div>
  );
}

export function AiAssistantPanel({
  className,
  currentYaml,
  onApplyYaml,
  projectId,
  pipelineId,
  repoUrl,
  branch,
  onClose,
}: AiAssistantPanelProps) {
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [input, setInput] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [liveSteps, setLiveSteps] = React.useState<AiAssistantStep[]>([]);
  const [undo, setUndo] = React.useState<UndoState | null>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const yamlRef = React.useRef(currentYaml);
  yamlRef.current = currentYaml;
  /** The request in flight, if any. */
  const requestRef = React.useRef<AbortController | null>(null);

  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, liveSteps]);

  // Leaving the page stops the request.
  React.useEffect(() => {
    return () => {
      requestRef.current?.abort();
      requestRef.current = null;
    };
  }, []);

  async function sendMessage(prompt: string) {
    if (!prompt.trim() || loading) return;

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: prompt.trim(),
    };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);
    setLiveSteps([]);

    const latestYaml = yamlRef.current;
    const received: AiAssistantStep[] = [];
    const controller = new AbortController();
    requestRef.current = controller;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, REQUEST_TIMEOUT_MS);

    try {
      const openProposal = [...messages]
        .reverse()
        .find((m) => m.proposal && !m.decision);
      const history: AiChatMessage[] = messages.map((m) => ({
        role: m.role,
        content: historyContent(m, m === openProposal),
      }));

      const resp = await aiAssistantApi.stream(
        {
          prompt: prompt.trim(),
          current_yaml: latestYaml || null,
          project_id: projectId || null,
          pipeline_id: pipelineId || null,
          repo_url: repoUrl || null,
          branch: branch || null,
          history: history.length > 0 ? history : undefined,
        },
        {
          signal: controller.signal,
          onStep: (step) => {
            received.push(step);
            setLiveSteps([...received]);
          },
        },
      );

      // Cleared or left while the answer was on its way.
      if (requestRef.current !== controller) return;

      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: resp.reply,
          steps: resp.steps,
          proposal: resp.proposal,
          limitReached: resp.limit_reached,
          baseYaml: latestYaml,
        },
      ]);
    } catch (err) {
      if (requestRef.current !== controller) return;
      const detail = timedOut
        ? "The assistant took too long to answer. Please try again."
        : err instanceof Error && err.message
          ? err.message
          : "An unexpected error occurred. Please try again.";
      toast.error(detail);
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: "assistant",
          content: detail,
          steps: received,
        },
      ]);
    } finally {
      clearTimeout(timer);
      if (requestRef.current === controller) {
        requestRef.current = null;
        setLoading(false);
        setLiveSteps([]);
        inputRef.current?.focus();
      }
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  }

  function clearChat() {
    requestRef.current?.abort();
    requestRef.current = null;
    setLoading(false);
    setLiveSteps([]);
    setMessages([]);
    setUndo(null);
  }

  function setDecision(id: string, decision: ProposalDecision | undefined) {
    setMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, decision } : m)),
    );
  }

  function applyProposal(msg: Message) {
    if (!onApplyYaml || !msg.proposal) return;
    setUndo({
      messageId: msg.id,
      before: yamlRef.current,
      after: msg.proposal.yaml,
    });
    onApplyYaml(msg.proposal.yaml);
    setDecision(msg.id, "applied");
  }

  function undoApply() {
    if (!onApplyYaml || !undo) return;
    onApplyYaml(undo.before);
    setDecision(undo.messageId, undefined);
    setUndo(null);
  }

  return (
    <div className={cn("flex h-full flex-col", className)}>
      {/* Header */}
      <div className="flex items-center justify-between border-b px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
            <Sparkles className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-none">AI Assistant</h3>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              Pipeline builder
            </p>
          </div>
        </div>
        {onClose && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </Button>
        )}
      </div>

      {/* Messages */}
      <ScrollArea
        ref={scrollRef}
        className="min-h-0 flex-1"
      >
        {messages.length === 0 ? (
          <div className="p-5 space-y-5">
            <div className="text-center py-8">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10">
                <Sparkles className="h-6 w-6 text-primary/70" />
              </div>
              <p className="text-sm font-medium">Pipeline AI Assistant</p>
              <p className="mx-auto mt-1.5 max-w-xs text-xs leading-relaxed text-muted-foreground">
                Describe what you want. I&apos;ll edit the pipeline YAML and
                show you the changes before anything reaches the editor.
              </p>
            </div>
            <div className="space-y-1.5">
              <p className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground px-1">
                Quick prompts
              </p>
              {QUICK_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => sendMessage(prompt)}
                  className="w-full rounded-lg border px-3.5 py-2.5 text-left text-sm hover:bg-muted/50 transition-colors"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="p-5 space-y-5">
            {messages.map((msg) => (
              <div key={msg.id} className="flex gap-3">
                <div
                  className={cn(
                    "mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full",
                    msg.role === "user"
                      ? "bg-primary/10 text-primary"
                      : "bg-muted text-muted-foreground",
                  )}
                >
                  {msg.role === "user" ? (
                    <User className="h-3.5 w-3.5" />
                  ) : (
                    <Bot className="h-3.5 w-3.5" />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  {msg.steps && <StepSummary steps={msg.steps} />}
                  <ReplyText text={msg.content} />
                  {msg.proposal && (
                    <AiProposalCard
                      proposal={msg.proposal}
                      decision={msg.decision}
                      stale={
                        msg.baseYaml !== undefined &&
                        currentYaml !== msg.baseYaml
                      }
                      limitReached={msg.limitReached ?? false}
                      canUndo={
                        undo?.messageId === msg.id &&
                        currentYaml === undo.after
                      }
                      onApply={
                        onApplyYaml ? () => applyProposal(msg) : undefined
                      }
                      onDiscard={() => setDecision(msg.id, "discarded")}
                      onUndo={undoApply}
                    />
                  )}
                  {!msg.proposal && msg.limitReached && (
                    <p className="mt-1.5 text-xs text-warning">
                      The assistant stopped early.
                    </p>
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                  <Bot className="h-3.5 w-3.5" />
                </div>
                <div className="min-w-0 flex-1 space-y-1.5">
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    {liveSteps.length > 0 ? "Working..." : "Thinking..."}
                  </div>
                  <StepRows steps={liveSteps} />
                </div>
              </div>
            )}
          </div>
        )}
      </ScrollArea>

      {/* Input */}
      <div className="border-t p-4">
        <div className="relative">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe the pipeline you need..."
            rows={3}
            className="w-full resize-none rounded-lg border bg-transparent px-3.5 py-2.5 pr-12 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <button
            type="button"
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || loading}
            className="absolute bottom-2.5 right-2.5 rounded-lg p-2 text-primary hover:bg-primary/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
        <div className="mt-1.5 flex items-center justify-between">
          <p className="text-[10px] text-muted-foreground">
            Press Enter to send · Shift+Enter for new line
          </p>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={clearChat}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <Trash2 className="h-3 w-3" />
              Clear
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
