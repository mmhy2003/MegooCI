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
}: AiAssistantPanelProps) {
  const [messages, setMessages] = React.useState<Message[]>([]);
  const [input, setInput] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);

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
    setMessages((prev) => [...prev, userMsg]);
    setInput("");
    setLoading(true);

    try {
      const history: AiChatMessage[] = messages.map((m) => ({
        role: m.role,
        content: m.content,
      }));

      const resp = await aiAssistantApi.ask({
        prompt: prompt.trim(),
        current_yaml: currentYaml || null,
        project_id: projectId || null,
        history: history.length > 0 ? history : undefined,
      });

      const assistantMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: resp.reply,
        yaml: resp.yaml,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch {
      toast.error("AI assistant failed to respond");
      const errorMsg: Message = {
        id: crypto.randomUUID(),
        role: "assistant",
        content: "Sorry, I couldn't process that request. Please try again.",
      };
      setMessages((prev) => [...prev, errorMsg]);
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
    <div className={cn("flex flex-col", className)}>
      {/* Header */}
      <div className="flex items-center border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <Sparkles className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">AI Assistant</h3>
        </div>
      </div>

      {/* Messages */}
      <ScrollArea
        ref={scrollRef}
        maxHeight="calc(100vh - 340px)"
        className="flex-1"
      >
        {messages.length === 0 ? (
          <div className="p-4 space-y-4">
            <div className="text-center py-6">
              <Sparkles className="mx-auto h-8 w-8 text-muted-foreground/50 mb-2" />
              <p className="text-sm font-medium">Pipeline AI Assistant</p>
              <p className="text-xs text-muted-foreground mt-1">
                Describe what you want and I'll generate the YAML for you.
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
                  className="w-full rounded-md border px-3 py-2 text-left text-xs hover:bg-muted/50 transition-colors"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="p-4 space-y-4">
            {messages.map((msg) => (
              <div key={msg.id} className="flex gap-2.5">
                <div
                  className={cn(
                    "mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full",
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
                    <p className="mt-1 text-xs text-muted-foreground leading-relaxed">
                      {msg.content.replace(/```[\s\S]*?```/g, "").trim()}
                    </p>
                  )}
                </div>
              </div>
            ))}
            {loading && (
              <div className="flex gap-2.5">
                <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
                  <Bot className="h-3.5 w-3.5" />
                </div>
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  Generating...
                </div>
              </div>
            )}
          </div>
        )}
      </ScrollArea>

      {/* Input */}
      <div className="border-t p-3">
        <div className="relative">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Describe the pipeline you need..."
            rows={2}
            className="w-full resize-none rounded-md border bg-transparent px-3 py-2 pr-10 text-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          />
          <button
            type="button"
            onClick={() => sendMessage(input)}
            disabled={!input.trim() || loading}
            className="absolute bottom-2 right-2 rounded-md p-1.5 text-primary hover:bg-primary/10 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </button>
        </div>
        <div className="mt-1 flex items-center justify-between">
          <p className="text-[10px] text-muted-foreground">
            Press Enter to send, Shift+Enter for new line
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
