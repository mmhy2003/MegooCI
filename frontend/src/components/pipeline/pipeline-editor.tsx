"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { BookOpen, Sparkles, Variable } from "lucide-react";
import { Button } from "@/components/ui/button";
import { YamlEditor } from "@/components/ui/yaml-editor";

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
  docsOpen = false,
  onToggleDocs,
  varsOpen = false,
  onToggleVars,
}: PipelineEditorProps) {
  return (
    <div className={cn("space-y-2", className)}>
      {/* Toolbar */}
      <div className="flex items-center justify-end">
        <div className="flex gap-1.5">
          {onToggleDocs && (
            <Button
              type="button"
              variant={docsOpen ? "default" : "outline"}
              size="sm"
              onClick={onToggleDocs}
              className="h-7 gap-1.5 text-xs"
            >
              <BookOpen className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Docs</span>
            </Button>
          )}
          {onToggleVars && (
            <Button
              type="button"
              variant={varsOpen ? "default" : "outline"}
              size="sm"
              onClick={onToggleVars}
              className="h-7 gap-1.5 text-xs"
            >
              <Variable className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Vars</span>
            </Button>
          )}
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

      {/* Editor */}
      <YamlEditor
        value={value}
        onChange={onChange}
        readOnly={readOnly}
        minHeight={minHeight}
        maxHeight="calc(100vh - 300px)"
        placeholder={placeholder}
      />
    </div>
  );
}
