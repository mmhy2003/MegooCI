"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import {
  Search,
  Copy,
  Maximize2,
  Minimize2,
  ArrowDown,
  Check,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export interface LogLine {
  timestamp?: string;
  text: string;
  stream?: "stdout" | "stderr" | "system";
}

interface BuildLogViewerProps {
  lines: LogLine[];
  className?: string;
}

export function BuildLogViewer({ lines, className }: BuildLogViewerProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);

  const [follow, setFollow] = React.useState(true);
  const [fullscreen, setFullscreen] = React.useState(false);
  const [searchOpen, setSearchOpen] = React.useState(false);
  const [searchTerm, setSearchTerm] = React.useState("");
  const [copied, setCopied] = React.useState(false);

  // When set to true the next scroll event(s) that arrive are from our own
  // programmatic scroll — we ignore them so they cannot accidentally disable
  // follow-mode.
  const isProgrammaticScroll = React.useRef(false);
  // rAF handle for the programmatic-scroll flag reset.
  const programmaticScrollRAF = React.useRef<number>(undefined);

  // ─── Auto-scroll to the bottom whenever new lines arrive ─────────────────
  // Scroll the log container itself — NOT scrollIntoView. scrollIntoView would
  // also scroll every scrollable ancestor (the page's <main>), dragging the
  // whole page to the bottom. Mutating scrollTop affects only this element.
  React.useEffect(() => {
    const container = containerRef.current;
    if (!follow || !container) return;

    // Setting scrollTop fires an async scroll event — mark it programmatic so
    // handleScroll doesn't mistake it for the user scrolling up and disable
    // follow-mode (this is what previously made auto-scroll "stop").
    isProgrammaticScroll.current = true;
    // Cancel any pending reset first.
    if (programmaticScrollRAF.current !== undefined) {
      cancelAnimationFrame(programmaticScrollRAF.current);
    }

    container.scrollTop = container.scrollHeight;

    // Reset the flag after the browser has flushed the layout and fired any
    // resulting scroll events (two rAF ticks is reliably enough).
    programmaticScrollRAF.current = requestAnimationFrame(() => {
      programmaticScrollRAF.current = requestAnimationFrame(() => {
        isProgrammaticScroll.current = false;
      });
    });
  }, [lines, follow]);

  // Cleanup rAF on unmount.
  React.useEffect(() => {
    return () => {
      if (programmaticScrollRAF.current !== undefined) {
        cancelAnimationFrame(programmaticScrollRAF.current);
      }
    };
  }, []);

  // ─── Keyboard shortcuts ──────────────────────────────────────────────────
  React.useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if ((e.ctrlKey || e.metaKey) && e.key === "f") {
        e.preventDefault();
        setSearchOpen((prev) => !prev);
      }
      if (e.key === "Escape") {
        if (fullscreen) setFullscreen(false);
        else setSearchOpen(false);
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [fullscreen]);

  // ─── User-scroll detection ───────────────────────────────────────────────
  // Only disable follow-mode when the user EXPLICITLY scrolls up — ignore any
  // scroll events that were caused by our own scrollIntoView calls.
  function handleScroll() {
    if (isProgrammaticScroll.current) return;
    if (!containerRef.current) return;

    const { scrollTop, scrollHeight, clientHeight } = containerRef.current;
    // Use a generous threshold (80 px) to tolerate sub-pixel rounding,
    // momentum scrolling overshoot, and browser zoom levels.
    const atBottom = scrollHeight - scrollTop - clientHeight < 80;
    if (!atBottom && follow) {
      setFollow(false);
    } else if (atBottom && !follow) {
      // If the user manually scrolled back to the bottom, re-enable.
      setFollow(true);
    }
  }

  // ─── Toolbar actions ─────────────────────────────────────────────────────
  async function handleCopy() {
    const text = lines.map((l) => l.text).join("\n");
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function handleFollowToggle() {
    const next = !follow;
    setFollow(next);
    const container = containerRef.current;
    if (next && container) {
      isProgrammaticScroll.current = true;
      container.scrollTo({ top: container.scrollHeight, behavior: "smooth" });
      programmaticScrollRAF.current = requestAnimationFrame(() => {
        programmaticScrollRAF.current = requestAnimationFrame(() => {
          isProgrammaticScroll.current = false;
        });
      });
    }
  }

  // ─── Search filter ───────────────────────────────────────────────────────
  const filtered = searchTerm
    ? lines.filter((l) =>
        l.text.toLowerCase().includes(searchTerm.toLowerCase()),
      )
    : lines;

  return (
    <div
      className={cn(
        "flex flex-col rounded-lg border bg-[#0d1117] text-[#c9d1d9]",
        fullscreen && "fixed inset-0 z-50 rounded-none",
        className,
      )}
    >
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2 border-b border-[#21262d] px-2 py-1.5 sm:px-3">
        <span className="shrink-0 text-xs font-medium text-[#8b949e]">
          <span className="hidden sm:inline">Build Logs </span>
          <span className="sm:hidden">Logs </span>
          ({lines.length})
        </span>
        <div className="flex items-center gap-1">
          {searchOpen && (
            <Input
              className="h-7 w-32 border-[#30363d] bg-[#161b22] text-xs text-[#c9d1d9] placeholder:text-[#484f58] sm:w-48"
              placeholder="Search…"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              autoFocus
            />
          )}
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-[#8b949e] hover:bg-[#21262d] hover:text-[#c9d1d9]"
            onClick={() => setSearchOpen(!searchOpen)}
          >
            <Search className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-[#8b949e] hover:bg-[#21262d] hover:text-[#c9d1d9]"
            onClick={handleCopy}
          >
            {copied ? (
              <Check className="h-3.5 w-3.5 text-emerald-400" />
            ) : (
              <Copy className="h-3.5 w-3.5" />
            )}
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className={cn(
              "h-7 w-7 hover:bg-[#21262d] hover:text-[#c9d1d9]",
              follow ? "text-cyan-400" : "text-[#8b949e]",
            )}
            onClick={handleFollowToggle}
            title={follow ? "Auto-scroll on (click to pause)" : "Auto-scroll off (click to resume)"}
          >
            <ArrowDown className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-[#8b949e] hover:bg-[#21262d] hover:text-[#c9d1d9]"
            onClick={() => setFullscreen(!fullscreen)}
          >
            {fullscreen ? (
              <Minimize2 className="h-3.5 w-3.5" />
            ) : (
              <Maximize2 className="h-3.5 w-3.5" />
            )}
          </Button>
        </div>
      </div>

      {/* Log content */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className={cn(
          "flex-1 overflow-auto p-2 font-mono text-xs leading-5 sm:p-3",
          fullscreen ? "max-h-none" : "max-h-[60vh] sm:max-h-[500px]",
        )}
      >
        {filtered.length === 0 ? (
          <p className="py-8 text-center text-[#484f58]">
            {lines.length === 0
              ? "Waiting for logs…"
              : "No matching log lines"}
          </p>
        ) : (
          filtered.map((line, idx) => (
            <div key={idx} className="flex gap-2 hover:bg-[#161b22] sm:gap-3">
              <span className="w-6 shrink-0 select-none text-right text-[#484f58] sm:w-10">
                {idx + 1}
              </span>
              {line.timestamp && (
                <span className="hidden shrink-0 text-[#484f58] sm:inline">
                  {line.timestamp}
                </span>
              )}
              <span
                className={cn(
                  "flex-1 whitespace-pre-wrap break-all",
                  line.stream === "stderr"
                    ? "text-red-400"
                    : line.stream === "system"
                      ? "text-cyan-400 italic"
                      : "text-[#c9d1d9]",
                )}
              >
                {searchTerm
                  ? highlightSearch(line.text, searchTerm)
                  : line.text}
              </span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function highlightSearch(text: string, term: string): React.ReactNode {
  if (!term) return text;
  const regex = new RegExp(`(${escapeRegex(term)})`, "gi");
  const parts = text.split(regex);
  return parts.map((part, i) =>
    regex.test(part) ? (
      <mark key={i} className="bg-yellow-500/30 text-yellow-200 rounded-sm px-0.5">
        {part}
      </mark>
    ) : (
      part
    ),
  );
}

function escapeRegex(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
