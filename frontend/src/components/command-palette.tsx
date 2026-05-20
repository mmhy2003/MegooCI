"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import {
  Search,
  FolderKanban,
  GitBranch,
  Hammer,
  FileArchive,
  Loader2,
  CornerDownLeft,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { searchApi, type SearchHit } from "@/lib/api";

const TYPE_META: Record<
  string,
  { icon: React.ElementType; label: string; color: string }
> = {
  project: {
    icon: FolderKanban,
    label: "Project",
    color: "text-blue-500",
  },
  pipeline: {
    icon: GitBranch,
    label: "Pipeline",
    color: "text-violet-500",
  },
  build: {
    icon: Hammer,
    label: "Build",
    color: "text-amber-500",
  },
  artifact: {
    icon: FileArchive,
    label: "Artifact",
    color: "text-emerald-500",
  },
};

interface CommandPaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  const router = useRouter();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [query, setQuery] = React.useState("");
  const [results, setResults] = React.useState<SearchHit[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [activeIndex, setActiveIndex] = React.useState(0);
  const debounceRef = React.useRef<ReturnType<typeof setTimeout>>(undefined);

  React.useEffect(() => {
    if (open) {
      setQuery("");
      setResults([]);
      setActiveIndex(0);
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }, [open]);

  React.useEffect(() => {
    if (!query.trim()) {
      setResults([]);
      return;
    }

    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const res = await searchApi.search(query.trim(), 8);
        setResults(res.results);
        setActiveIndex(0);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, 200);

    return () => clearTimeout(debounceRef.current);
  }, [query]);

  const navigate = React.useCallback(
    (hit: SearchHit) => {
      onOpenChange(false);
      router.push(hit.url);
    },
    [onOpenChange, router],
  );

  const handleKeyDown = React.useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActiveIndex((i) => (i + 1) % Math.max(results.length, 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActiveIndex((i) =>
          i <= 0 ? Math.max(results.length - 1, 0) : i - 1,
        );
      } else if (e.key === "Enter" && results[activeIndex]) {
        e.preventDefault();
        navigate(results[activeIndex]);
      } else if (e.key === "Escape") {
        e.preventDefault();
        onOpenChange(false);
      }
    },
    [results, activeIndex, navigate, onOpenChange],
  );

  // Global Cmd/Ctrl+K listener
  React.useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        onOpenChange(!open);
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  if (!open) return null;

  const grouped = groupByType(results);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center pt-[15vh]">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/60 backdrop-blur-sm"
        onClick={() => onOpenChange(false)}
      />

      {/* Palette */}
      <div className="relative z-50 w-full max-w-lg overflow-hidden rounded-xl border bg-card shadow-2xl">
        {/* Search input */}
        <div className="flex items-center gap-3 border-b px-4">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <input
            ref={inputRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search projects, pipelines, builds, artifacts..."
            className="flex-1 bg-transparent py-3.5 text-sm outline-none placeholder:text-muted-foreground"
            autoComplete="off"
            spellCheck={false}
          />
          {loading && (
            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
          )}
          <kbd className="hidden rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground sm:inline-block">
            ESC
          </kbd>
        </div>

        {/* Results */}
        <div className="max-h-80 overflow-y-auto overscroll-contain p-2">
          {!query.trim() && (
            <div className="px-3 py-8 text-center text-sm text-muted-foreground">
              Start typing to search across your CI resources...
            </div>
          )}

          {query.trim() && !loading && results.length === 0 && (
            <div className="px-3 py-8 text-center text-sm text-muted-foreground">
              No results for &ldquo;{query}&rdquo;
            </div>
          )}

          {grouped.map(([type, hits]) => {
            const meta = TYPE_META[type] || TYPE_META.project;
            return (
              <div key={type}>
                <div className="px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  {meta.label}s
                </div>
                {hits.map((hit) => {
                  const idx = results.indexOf(hit);
                  const Icon = meta.icon;
                  const isActive = idx === activeIndex;
                  return (
                    <button
                      key={hit.id}
                      onMouseEnter={() => setActiveIndex(idx)}
                      onClick={() => navigate(hit)}
                      className={cn(
                        "flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm transition-colors",
                        isActive
                          ? "bg-accent text-accent-foreground"
                          : "text-foreground hover:bg-accent/50",
                      )}
                    >
                      <Icon className={cn("h-4 w-4 shrink-0", meta.color)} />
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">{hit.title}</div>
                        {hit.subtitle && (
                          <div className="truncate text-xs text-muted-foreground">
                            {hit.subtitle}
                          </div>
                        )}
                      </div>
                      {isActive && (
                        <CornerDownLeft className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                      )}
                    </button>
                  );
                })}
              </div>
            );
          })}
        </div>

        {/* Footer */}
        {results.length > 0 && (
          <div className="flex items-center gap-4 border-t px-4 py-2 text-[11px] text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
                &uarr;&darr;
              </kbd>
              Navigate
            </span>
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
                &crarr;
              </kbd>
              Open
            </span>
            <span className="inline-flex items-center gap-1">
              <kbd className="rounded bg-muted px-1 py-0.5 font-mono text-[10px]">
                esc
              </kbd>
              Close
            </span>
          </div>
        )}
      </div>
    </div>
  );
}

function groupByType(hits: SearchHit[]): [string, SearchHit[]][] {
  const order = ["project", "pipeline", "build", "artifact"];
  const map = new Map<string, SearchHit[]>();
  for (const hit of hits) {
    const list = map.get(hit.type) ?? [];
    list.push(hit);
    map.set(hit.type, list);
  }
  return order
    .filter((t) => map.has(t))
    .map((t) => [t, map.get(t)!]);
}
