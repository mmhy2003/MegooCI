"use client";

import * as React from "react";
import { ChevronDown } from "lucide-react";
import type { Project } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** A searchable checkbox list of projects, opened from a select-like button. */
export function ProjectMultiSelect({
  projects,
  exclude,
  value,
  onChange,
}: {
  projects: Project[];
  exclude: Set<string>;
  value: string[];
  onChange: (ids: string[]) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const ref = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const q = query.trim().toLowerCase();
  const available = projects
    .filter((p) => !exclude.has(p.id) && (!q || p.name.toLowerCase().includes(q)))
    .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }));
  const chosen = new Set(value);
  const toggle = (id: string) =>
    onChange(chosen.has(id) ? value.filter((v) => v !== id) : [...value, id]);

  return (
    <div
      ref={ref}
      className="relative min-w-0 flex-1"
      onKeyDown={(e) => {
        // Escape closes the list only, not a dialog this sits in.
        if (e.key === "Escape" && open) {
          e.stopPropagation();
          setOpen(false);
        }
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-background px-3 text-sm"
      >
        <span className={cn("truncate", value.length === 0 && "text-muted-foreground")}>
          {value.length === 0
            ? "Add projects… pick one or more"
            : `${value.length} project${value.length === 1 ? "" : "s"} selected`}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </button>
      {open && (
        <div className="absolute bottom-full z-20 mb-1 w-full rounded-md border bg-popover p-2 shadow-lg sm:bottom-auto sm:top-full sm:mb-0 sm:mt-1">
          <Input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search projects…"
            className="mb-2"
          />
          <div className="max-h-60 overflow-y-auto">
            {available.length === 0 && (
              <p className="px-2 py-3 text-center text-xs text-muted-foreground">No projects to add</p>
            )}
            {available.map((p) => (
              <label
                key={p.id}
                className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-muted/60"
              >
                <input
                  type="checkbox"
                  checked={chosen.has(p.id)}
                  onChange={() => toggle(p.id)}
                  className="h-4 w-4 accent-primary"
                />
                <span className="truncate">{p.name}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
