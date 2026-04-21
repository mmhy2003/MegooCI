"use client";

import * as React from "react";
import CodeMirror, { type ReactCodeMirrorProps } from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";
import { EditorView } from "@codemirror/view";
import { type Extension } from "@codemirror/state";
import { HighlightStyle, syntaxHighlighting } from "@codemirror/language";
import { tags } from "@lezer/highlight";

const cyberpunkLightHighlight = HighlightStyle.define([
  { tag: tags.keyword, color: "hsl(345, 82%, 48%)" },
  { tag: tags.string, color: "hsl(158, 84%, 30%)" },
  { tag: tags.number, color: "hsl(263, 60%, 45%)" },
  { tag: tags.bool, color: "hsl(263, 60%, 45%)" },
  { tag: tags.null, color: "hsl(262, 12%, 46%)" },
  { tag: tags.comment, color: "hsl(262, 12%, 46%)", fontStyle: "italic" },
  { tag: tags.propertyName, color: "hsl(176, 100%, 30%)" },
  { tag: tags.punctuation, color: "hsl(262, 12%, 46%)" },
  { tag: tags.meta, color: "hsl(345, 82%, 48%)" },
]);

const cyberpunkDarkHighlight = HighlightStyle.define([
  { tag: tags.keyword, color: "hsl(346, 100%, 58%)" },
  { tag: tags.string, color: "hsl(151, 100%, 44%)" },
  { tag: tags.number, color: "hsl(280, 80%, 70%)" },
  { tag: tags.bool, color: "hsl(280, 80%, 70%)" },
  { tag: tags.null, color: "hsl(262, 14%, 55%)" },
  { tag: tags.comment, color: "hsl(262, 14%, 55%)", fontStyle: "italic" },
  { tag: tags.propertyName, color: "hsl(176, 100%, 50%)" },
  { tag: tags.punctuation, color: "hsl(262, 14%, 55%)" },
  { tag: tags.meta, color: "hsl(346, 100%, 58%)" },
]);

const lightEditorTheme = EditorView.theme(
  {
    "&": {
      backgroundColor: "hsl(260, 40%, 100%)",
      color: "hsl(263, 70%, 8%)",
      fontSize: "13px",
      borderRadius: "calc(var(--radius) - 2px)",
      border: "1px solid hsl(var(--border))",
    },
    "&.cm-focused": {
      outline: "2px solid hsl(var(--ring))",
      outlineOffset: "1px",
    },
    ".cm-scroller": {
      fontFamily:
        'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    },
    ".cm-cursor": { borderLeftColor: "hsl(176, 100%, 30%)" },
    ".cm-selectionBackground": { backgroundColor: "hsl(176, 100%, 30%, 0.15) !important" },
    ".cm-activeLine": { backgroundColor: "hsl(263, 25%, 93%, 0.5)" },
    ".cm-gutters": {
      backgroundColor: "hsl(260, 50%, 98%)",
      color: "hsl(262, 12%, 46%)",
      borderRight: "1px solid hsl(var(--border))",
    },
    ".cm-activeLineGutter": { backgroundColor: "hsl(263, 25%, 93%, 0.5)" },
  },
  { dark: false },
);

const darkEditorTheme = EditorView.theme(
  {
    "&": {
      backgroundColor: "hsl(262, 60%, 7%)",
      color: "hsl(176, 20%, 90%)",
      fontSize: "13px",
      borderRadius: "calc(var(--radius) - 2px)",
      border: "1px solid hsl(var(--border))",
    },
    "&.cm-focused": {
      outline: "2px solid hsl(var(--ring))",
      outlineOffset: "1px",
    },
    ".cm-scroller": {
      fontFamily:
        'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    },
    ".cm-cursor": { borderLeftColor: "hsl(176, 100%, 50%)" },
    ".cm-selectionBackground": { backgroundColor: "hsl(176, 100%, 50%, 0.15) !important" },
    ".cm-activeLine": { backgroundColor: "hsl(263, 35%, 13%, 0.5)" },
    ".cm-gutters": {
      backgroundColor: "hsl(264, 90%, 4%)",
      color: "hsl(262, 14%, 55%)",
      borderRight: "1px solid hsl(var(--border))",
    },
    ".cm-activeLineGutter": { backgroundColor: "hsl(263, 35%, 13%, 0.5)" },
  },
  { dark: true },
);

const lightTheme: Extension = [lightEditorTheme, syntaxHighlighting(cyberpunkLightHighlight)];
const darkTheme: Extension = [darkEditorTheme, syntaxHighlighting(cyberpunkDarkHighlight)];

interface YamlEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  minHeight?: string;
  maxHeight?: string;
  className?: string;
  placeholder?: string;
}

export function YamlEditor({
  value,
  onChange,
  readOnly = false,
  minHeight = "320px",
  maxHeight,
  className,
  placeholder,
}: YamlEditorProps) {
  const [isDark, setIsDark] = React.useState(false);

  React.useEffect(() => {
    const root = document.documentElement;
    setIsDark(root.classList.contains("dark"));
    const observer = new MutationObserver(() => {
      setIsDark(root.classList.contains("dark"));
    });
    observer.observe(root, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);

  const extensions = React.useMemo<Extension[]>(() => {
    const exts: Extension[] = [yaml()];
    if (readOnly) {
      exts.push(EditorView.editable.of(false));
    }
    return exts;
  }, [readOnly]);

  const handleChange = React.useCallback<
    NonNullable<ReactCodeMirrorProps["onChange"]>
  >(
    (val) => {
      onChange?.(val);
    },
    [onChange],
  );

  return (
    <div className={className}>
      <CodeMirror
        value={value}
        onChange={handleChange}
        theme={isDark ? darkTheme : lightTheme}
        extensions={extensions}
        readOnly={readOnly}
        placeholder={placeholder}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLineGutter: !readOnly,
          highlightActiveLine: !readOnly,
          foldGutter: true,
          bracketMatching: true,
          indentOnInput: true,
          tabSize: 2,
        }}
        minHeight={minHeight}
        maxHeight={maxHeight}
      />
    </div>
  );
}
