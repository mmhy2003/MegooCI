"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { BookOpen, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { DocsPanel } from "./docs-panel";

interface PipelineEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  minHeight?: string;
  className?: string;
  placeholder?: string;
  projectId?: string | null;
  /** Whether the external AI drawer is currently open (controls button highlight). */
  aiOpen?: boolean;
  /** Called when the user clicks the AI Assistant toolbar button. */
  onToggleAi?: () => void;
}

export function PipelineEditor({
  value,
  onChange,
  readOnly = false,
  minHeight = "400px",
  className,
  placeholder,
  aiOpen = false,
  onToggleAi,
}: PipelineEditorProps) {
  const [docsOpen, setDocsOpen] = React.useState(false);

  function handleInsert(yaml: string) {
    if (!onChange) return;
    const trimmed = value.trimEnd();
    const newContent = trimmed ? `${trimmed}\n\n${yaml}\n` : `${yaml}\n`;
    onChange(newContent);
  }

  return (
    <div className={cn("space-y-2", className)}>
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium">Pipeline definition</label>
        <div className="flex gap-1.5">
          <Button
            type="button"
            variant={docsOpen ? "default" : "outline"}
            size="sm"
            onClick={() => setDocsOpen((prev) => !prev)}
            className="h-7 gap-1.5 text-xs"
          >
            <BookOpen className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Docs</span>
          </Button>
          {!readOnly && onToggleAi && (
            <Button
              type="button"
              variant={aiOpen ? "default" : "outline"}
              size="sm"
              onClick={onToggleAi}
              className="h-7 gap-1.5 text-xs"
            >
              <Sparkles className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">AI Assistant</span>
            </Button>
          )}
        </div>
      </div>

      {/* Editor + Docs Panel */}
      <div className="flex gap-4">
        {/* Editor */}
        <div className={cn("min-w-0", docsOpen ? "flex-1" : "w-full")}>
          <YamlEditor
            value={value}
            onChange={onChange}
            readOnly={readOnly}
            minHeight={minHeight}
            maxHeight="calc(100vh - 300px)"
            placeholder={placeholder}
          />
        </div>

        {/* Docs Side Panel (desktop) */}
        {docsOpen && (
          <div className="hidden w-[380px] shrink-0 rounded-lg border bg-background shadow-sm lg:flex lg:flex-col">
            <div className="relative">
              <button
                type="button"
                onClick={() => setDocsOpen(false)}
                className="absolute right-2 top-2 z-10 rounded-sm p-0.5 text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
              <DocsPanel onInsert={readOnly ? undefined : handleInsert} />
            </div>
          </div>
        )}
      </div>

      {/* Docs panel below editor on mobile */}
      {docsOpen && (
        <div className="rounded-lg border bg-background shadow-sm lg:hidden">
          <div className="relative">
            <button
              type="button"
              onClick={() => setDocsOpen(false)}
              className="absolute right-2 top-2 z-10 rounded-sm p-0.5 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
            <DocsPanel onInsert={readOnly ? undefined : handleInsert} />
          </div>
        </div>
      )}
    </div>
  );
}
