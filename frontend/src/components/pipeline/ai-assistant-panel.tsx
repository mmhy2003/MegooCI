"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { aiAssistantApi, type AiChatMessage } from "@/lib/api";
import { toast } from "sonner";
import {
  Sparkles,
  Send,
  Copy,
  Check,
  Loader2,
  ArrowDownToLine,
  User,
  Bot,
  Trash2,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  yaml?: string | null;
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

function YamlBlock({
  yaml,
  onApply,
}: {
  yaml: string;
  onApply?: (yaml: string) => void;
}) {
  const [copied, setCopied] = React.useState(false);

  return (
    <div className="my-2 rounded-md border bg-muted/30 overflow-hidden">
      <div className="flex items-center justify-between border-b bg-muted/50 px-3 py-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
          Generated YAML
        </span>
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => {
              navigator.clipboard.writeText(yaml);
              setCopied(true);
              setTimeout(() => setCopied(false), 2000);
            }}
            className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
          >
            {copied ? (
              <Check className="h-3 w-3" />
            ) : (
              <Copy className="h-3 w-3" />
            )}
            {copied ? "Copied" : "Copy"}
          </button>
          {onApply && (
            <button
              type="button"
              onClick={() => onApply(yaml)}
              className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-primary hover:bg-primary/10 transition-colors"
            >
              <ArrowDownToLine className="h-3 w-3" />
              Apply to editor
            </button>
          )}
        </div>
      </div>
      <pre className="overflow-x-auto p-3 text-xs leading-relaxed max-h-72 overflow-y-auto">
        <code>{yaml}</code>
      </pre>
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
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);
  const yamlRef = React.useRef(currentYaml);
  yamlRef.current = currentYaml;

  React.useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  async function sendMessage(prompt: string) {
    if (!prompt.trim() || loading) return;

    const userMsg: Message = {
      id: crypto.randomUUID(),
      role: "user",
      content: prompt.trim(),
    };
    const streamingMsgId = crypto.randomUUID();
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    const latestYaml = yamlRef.current;

    try {
      const history: AiChatMessage[] = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      // Add a placeholder assistant message for streaming tokens
      setMessages((prev) => [
        ...prev,
        { id: streamingMsgId, role: "assistant", content: "", yaml: null },
      ]);

      const resp = await aiAssistantApi.askStream(
        {
          prompt: prompt.trim(),
          current_yaml: latestYaml || null,
          project_id: projectId || null,
          pipeline_id: pipelineId || null,
          repo_url: repoUrl || null,
          branch: branch || null,
          history: history.length > 0 ? history : undefined,
        },
        (token) => {
          // Update the streaming message with each token
          setMessages((prev) =>
            prev.map((m) =>
              m.id === streamingMsgId
                ? { ...m, content: m.content + token }
                : m,
            ),
          );
        },
      );

      // Replace streaming message with final response (includes extracted YAML)
      const assistantMsg: Message = {
        id: streamingMsgId,
        role: "assistant",
        content: resp.reply,
        yaml: resp.yaml,
      };
      setMessages((prev) =>
        prev.map((m) => (m.id === streamingMsgId ? assistantMsg : m)),
      );
    } catch (err) {
      const detail =
        err instanceof Error && err.message
          ? err.message
          : "An unexpected error occurred. Please try again.";
      toast.error(detail);
      // Replace or add error message
      setMessages((prev) => {
        const hasStreaming = prev.some((m) => m.id === streamingMsgId);
        const errorMsg: Message = {
          id: streamingMsgId,
          role: "assistant",
          content: detail,
        };
        if (hasStreaming) {
          return prev.map((m) => (m.id === streamingMsgId ? errorMsg : m));
        }
        return [...prev, errorMsg];
      });
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage(input);
    }
  }

  function clearChat() {
    setMessages([]);
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
                Describe what you want and I&apos;ll generate the YAML
                pipeline definition for you.
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
            {messages.map((msg) => {
              // Skip the streaming placeholder while it has no content —
              // the "Thinking..." indicator below handles that state.
              if (loading && msg.role === "assistant" && !msg.content && !msg.yaml) {
                return null;
              }
              return (
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
                  {msg.yaml ? (
                    <YamlBlock yaml={msg.yaml} onApply={onApplyYaml} />
                  ) : (
                    <div className="text-sm leading-relaxed whitespace-pre-wrap">
                      {msg.content}
                    </div>
                  )}
                  {msg.yaml && msg.content !== msg.yaml && (
                    <p className="mt-1.5 text-xs text-muted-foreground leading-relaxed">
                      {msg.content.replace(/```[\s\S]*?```/g, "").trim()}
                    </p>
                  )}
                </div>
              </div>
              );
            })}
            {loading && !messages.some((m) => m.role === "assistant" && m.id === messages[messages.length - 1]?.id && m.content) && (
              <div className="flex gap-3">
                <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                  <Bot className="h-3.5 w-3.5" />
                </div>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Thinking...
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
