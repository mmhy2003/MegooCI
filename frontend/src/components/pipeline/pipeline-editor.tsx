"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { BookOpen, Pencil, Sparkles, Variable } from "lucide-react";
import { Button } from "@/components/ui/button";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { pipelinesApi, type PipelineValidationError } from "@/lib/api";

// ── Shortcut helpers ────────────────────────────────────────────────────

function useIsMac() {
  const [isMac, setIsMac] = React.useState(false);
  React.useEffect(() => {
    setIsMac(navigator.platform?.toUpperCase().includes("MAC") ?? false);
  }, []);
  return isMac;
}

/** Tiny pill that renders a keyboard shortcut hint. */
function Kbd({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "hidden md:inline-flex items-center rounded border border-border/60 bg-muted/70 px-1 py-px text-[10px] font-mono leading-none text-muted-foreground",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

// ── Props ───────────────────────────────────────────────────────────────

interface PipelineEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  minHeight?: string;
  className?: string;
  placeholder?: string;
  projectId?: string | null;
  pipelineId?: string | null;
  /** Whether the external AI drawer is currently open (controls button highlight). */
  aiOpen?: boolean;
  /** Called when the user clicks the AI Assistant toolbar button. */
  onToggleAi?: () => void;
  /** Whether the external Docs drawer is currently open (controls button highlight). */
  docsOpen?: boolean;
  /** Called when the user clicks the Docs toolbar button. */
  onToggleDocs?: () => void;
  /** Whether the external Vars drawer is currently open (controls button highlight). */
  varsOpen?: boolean;
  /** Called when the user clicks the Vars toolbar button. */
  onToggleVars?: () => void;
  /** Called when the user presses Ctrl+S / Cmd+S to save. */
  onSave?: () => void;
  /** Called when the user presses Ctrl+Shift+E / Cmd+Shift+E to toggle edit mode. */
  onToggleEdit?: () => void;
  /** Called when the user presses Escape to cancel editing. */
  onCancelEdit?: () => void;
}

// ── Component ───────────────────────────────────────────────────────────

export function PipelineEditor({
  value,
  onChange,
  readOnly = false,
  minHeight = "400px",
  className,
  placeholder,
  aiOpen = false,
  onToggleAi,
  docsOpen = false,
  onToggleDocs,
  varsOpen = false,
  onToggleVars,
  onSave,
  onToggleEdit,
  onCancelEdit,
}: PipelineEditorProps) {
  const isMac = useIsMac();
  const mod = isMac ? "⌘" : "Ctrl";
  const alt = isMac ? "⌥" : "Alt";

  // Keep refs so the event handler always sees the latest callbacks
  // without forcing the effect to re-register.
  const callbacks = React.useRef({
    onSave,
    onToggleDocs,
    onToggleVars,
    onToggleAi,
    onToggleEdit,
    onCancelEdit,
  });
  callbacks.current = {
    onSave,
    onToggleDocs,
    onToggleVars,
    onToggleAi,
    onToggleEdit,
    onCancelEdit,
  };

  const [problems, setProblems] = React.useState<PipelineValidationError[]>([]);

  React.useEffect(() => {
    if (readOnly || !value.trim()) {
      setProblems([]);
      return;
    }
    const handle = setTimeout(async () => {
      try {
        const res = await pipelinesApi.validate(value);
        setProblems(res.errors);
      } catch {
        // A failing lint request must never block editing.
        setProblems([]);
      }
    }, 500);
    return () => clearTimeout(handle);
  }, [value, readOnly]);

  React.useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      const modKey = e.ctrlKey || e.metaKey;

      // Ctrl/Cmd + S → Save
      if (modKey && !e.shiftKey && e.key === "s") {
        e.preventDefault();
        callbacks.current.onSave?.();
        return;
      }

      // Alt + E → Toggle Edit
      if (e.altKey && !modKey && e.key.toLowerCase() === "e") {
        e.preventDefault();
        callbacks.current.onToggleEdit?.();
        return;
      }

      // Alt + D → Toggle Docs
      if (e.altKey && !modKey && e.key.toLowerCase() === "d") {
        e.preventDefault();
        callbacks.current.onToggleDocs?.();
        return;
      }

      // Alt + V → Toggle Vars
      if (e.altKey && !modKey && e.key.toLowerCase() === "v") {
        e.preventDefault();
        callbacks.current.onToggleVars?.();
        return;
      }

      // Alt + A → Toggle AI Assistant
      if (e.altKey && !modKey && e.key.toLowerCase() === "a") {
        e.preventDefault();
        callbacks.current.onToggleAi?.();
        return;
      }

      // Escape → Cancel Edit
      if (e.key === "Escape" && !e.ctrlKey && !e.metaKey && !e.shiftKey) {
        callbacks.current.onCancelEdit?.();
        return;
      }
    }

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  return (
    <div className={cn("space-y-2", className)}>
      {/* Toolbar */}
      <div className="flex items-center justify-between gap-2">
        {/* Left: edit toggle */}
        <div className="flex gap-1.5">
          {onToggleEdit && (
            <Button
              type="button"
              variant={readOnly ? "outline" : "default"}
              size="sm"
              onClick={onToggleEdit}
              className="h-7 gap-1.5 text-xs"
              title={`Toggle edit mode (${alt}+E)`}
            >
              <Pencil className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">{readOnly ? "Edit" : "Editing"}</span>
              <Kbd>{alt}E</Kbd>
            </Button>
          )}
        </div>

        {/* Right: panels + save */}
        <div className="flex gap-1.5">
          {onToggleDocs && (
            <Button
              type="button"
              variant={docsOpen ? "default" : "outline"}
              size="sm"
              onClick={onToggleDocs}
              className="h-7 gap-1.5 text-xs"
              title={`Toggle docs panel (${alt}+D)`}
            >
              <BookOpen className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Docs</span>
              <Kbd>{alt}D</Kbd>
            </Button>
          )}
          {onToggleVars && (
            <Button
              type="button"
              variant={varsOpen ? "default" : "outline"}
              size="sm"
              onClick={onToggleVars}
              className="h-7 gap-1.5 text-xs"
              title={`Toggle variables panel (${alt}+V)`}
            >
              <Variable className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Vars</span>
              <Kbd>{alt}V</Kbd>
            </Button>
          )}
          {onToggleAi && (
            <Button
              type="button"
              variant={aiOpen ? "default" : "outline"}
              size="sm"
              onClick={onToggleAi}
              className="h-7 gap-1.5 text-xs"
              title={`Toggle AI assistant (${alt}+A)`}
            >
              <Sparkles className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">AI Assistant</span>
              <Kbd>{alt}A</Kbd>
            </Button>
          )}
          {!readOnly && onSave && (
            <Button
              type="button"
              size="sm"
              onClick={onSave}
              className="h-7 gap-1.5 text-xs"
              title={`Save (${mod}+S)`}
            >
              <span>Save</span>
              <Kbd>{mod}S</Kbd>
            </Button>
          )}
        </div>
      </div>

      {/* Editor */}
      <YamlEditor
        value={value}
        onChange={onChange}
        readOnly={readOnly}
        minHeight={minHeight}
        maxHeight="calc(100vh - 300px)"
        placeholder={placeholder}
        diagnostics={problems.map((p) => ({
          line: p.line,
          column: p.column,
          message: p.message,
          severity: "error" as const,
        }))}
      />

      {!readOnly && problems.length > 0 && (
        <ul className="space-y-1 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs">
          {problems.map((p, i) => (
            <li key={i} className="font-mono text-destructive">
              {p.line != null ? `Line ${p.line}: ` : ""}
              {p.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
