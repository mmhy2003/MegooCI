"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { BookOpen, Sparkles, Variable, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { DocsPanel } from "./docs-panel";
import { VarsPanel } from "./vars-panel";

type SidePanel = "docs" | "vars" | null;

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
}

export function PipelineEditor({
  value,
  onChange,
  readOnly = false,
  minHeight = "400px",
  className,
  placeholder,
  projectId,
  pipelineId,
  aiOpen = false,
  onToggleAi,
}: PipelineEditorProps) {
  const [sidePanel, setSidePanel] = React.useState<SidePanel>(null);

  function togglePanel(panel: "docs" | "vars") {
    setSidePanel((prev) => (prev === panel ? null : panel));
  }

  function handleInsert(yaml: string) {
    if (!onChange) return;
    const trimmed = value.trimEnd();
    const newContent = trimmed ? `${trimmed}\n\n${yaml}\n` : `${yaml}\n`;
    onChange(newContent);
  }

  function handleInsertSnippet(snippet: string) {
    if (!onChange) return;
    // Insert the snippet at the end of the current content.
    // Users can also just copy and paste where needed.
    const trimmed = value.trimEnd();
    const newContent = trimmed ? `${trimmed} ${snippet}` : snippet;
    onChange(newContent);
  }

  const panelOpen = sidePanel !== null;

  return (
    <div className={cn("space-y-2", className)}>
      {/* Toolbar */}
      <div className="flex items-center justify-between">
        <label className="text-sm font-medium">Pipeline definition</label>
        <div className="flex gap-1.5">
          <Button
            type="button"
            variant={sidePanel === "docs" ? "default" : "outline"}
            size="sm"
            onClick={() => togglePanel("docs")}
            className="h-7 gap-1.5 text-xs"
          >
            <BookOpen className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Docs</span>
          </Button>
          <Button
            type="button"
            variant={sidePanel === "vars" ? "default" : "outline"}
            size="sm"
            onClick={() => togglePanel("vars")}
            className="h-7 gap-1.5 text-xs"
          >
            <Variable className="h-3.5 w-3.5" />
            <span className="hidden sm:inline">Vars</span>
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

      {/* Editor + Side Panel */}
      <div className="flex gap-4">
        {/* Editor */}
        <div className={cn("min-w-0", panelOpen ? "flex-1" : "w-full")}>
          <YamlEditor
            value={value}
            onChange={onChange}
            readOnly={readOnly}
            minHeight={minHeight}
            maxHeight="calc(100vh - 300px)"
            placeholder={placeholder}
          />
        </div>

        {/* Side Panel (desktop) */}
        {panelOpen && (
          <div className="hidden w-[380px] shrink-0 rounded-lg border bg-background shadow-sm lg:flex lg:flex-col">
            <div className="relative">
              <button
                type="button"
                onClick={() => setSidePanel(null)}
                className="absolute right-2 top-2 z-10 rounded-sm p-0.5 text-muted-foreground hover:text-foreground transition-colors"
              >
                <X className="h-4 w-4" />
              </button>
              {sidePanel === "docs" && (
                <DocsPanel onInsert={readOnly ? undefined : handleInsert} />
              )}
              {sidePanel === "vars" && (
                <VarsPanel
                  projectId={projectId}
                  pipelineId={pipelineId}
                  onInsert={readOnly ? undefined : handleInsertSnippet}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* Side Panel below editor on mobile */}
      {panelOpen && (
        <div className="rounded-lg border bg-background shadow-sm lg:hidden">
          <div className="relative">
            <button
              type="button"
              onClick={() => setSidePanel(null)}
              className="absolute right-2 top-2 z-10 rounded-sm p-0.5 text-muted-foreground hover:text-foreground transition-colors"
            >
              <X className="h-4 w-4" />
            </button>
            {sidePanel === "docs" && (
              <DocsPanel onInsert={readOnly ? undefined : handleInsert} />
            )}
            {sidePanel === "vars" && (
              <VarsPanel
                projectId={projectId}
                pipelineId={pipelineId}
                onInsert={readOnly ? undefined : handleInsertSnippet}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
