"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { BookOpen, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { YamlEditor } from "@/components/ui/yaml-editor";
import { DocsPanel } from "./docs-panel";
import { AiAssistantPanel } from "./ai-assistant-panel";

type SidePanel = "docs" | "ai" | null;

interface PipelineEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  minHeight?: string;
  className?: string;
  placeholder?: string;
  projectId?: string | null;
}

export function PipelineEditor({
  value,
  onChange,
  readOnly = false,
  minHeight = "400px",
  className,
  placeholder,
  projectId,
}: PipelineEditorProps) {
  const [sidePanel, setSidePanel] = React.useState<SidePanel>(null);

  function togglePanel(panel: SidePanel) {
    setSidePanel((prev) => (prev === panel ? null : panel));
  }

  function handleInsert(yaml: string) {
    if (!onChange) return;
    const trimmed = value.trimEnd();
    const newContent = trimmed ? `${trimmed}\n\n${yaml}\n` : `${yaml}\n`;
    onChange(newContent);
  }

  function handleApplyYaml(yaml: string) {
    if (!onChange) return;
    onChange(yaml);
  }

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
          {!readOnly && (
            <Button
              type="button"
              variant={sidePanel === "ai" ? "default" : "outline"}
              size="sm"
              onClick={() => togglePanel("ai")}
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
        <div className={cn("min-w-0", sidePanel ? "flex-1" : "w-full")}>
          <YamlEditor
            value={value}
            onChange={onChange}
            readOnly={readOnly}
            minHeight={minHeight}
            maxHeight="calc(100vh - 300px)"
            placeholder={placeholder}
          />
        </div>

        {/* Side Panel */}
        {sidePanel && (
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
              {sidePanel === "ai" && (
                <AiAssistantPanel
                  currentYaml={value}
                  onApplyYaml={handleApplyYaml}
                  projectId={projectId}
                />
              )}
            </div>
          </div>
        )}
      </div>

      {/* Mobile panel as sheet below the editor */}
      {sidePanel && (
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
            {sidePanel === "ai" && (
              <AiAssistantPanel
                currentYaml={value}
                onApplyYaml={handleApplyYaml}
                projectId={projectId}
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}
