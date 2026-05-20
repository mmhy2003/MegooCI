"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import {
  Lock,
  Variable,
  Copy,
  Check,
  ChevronDown,
  ChevronRight,
  Loader2,
  AlertCircle,
  X,
  Braces,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { secretsApi, envVarsApi, type Secret, type EnvVar } from "@/lib/api";

interface VarsPanelProps {
  className?: string;
  projectId?: string | null;
  pipelineId?: string | null;
  onInsert?: (text: string) => void;
  /** Called when the user clicks the close button in the header. */
  onClose?: () => void;
}

function CopySnippet({
  snippet,
  onInsert,
}: {
  snippet: string;
  onInsert?: (text: string) => void;
}) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = React.useCallback(() => {
    navigator.clipboard.writeText(snippet).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [snippet]);

  return (
    <div className="flex items-center gap-1">
      <code className="flex-1 truncate rounded bg-muted/60 px-2 py-1 text-xs font-mono text-foreground">
        {snippet}
      </code>
      <button
        type="button"
        onClick={handleCopy}
        title="Copy to clipboard"
        className="shrink-0 rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
      >
        {copied ? <Check className="h-3 w-3 text-green-500" /> : <Copy className="h-3 w-3" />}
      </button>
      {onInsert && (
        <button
          type="button"
          onClick={() => onInsert(snippet)}
          className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium text-primary hover:bg-primary/10 transition-colors"
        >
          Insert
        </button>
      )}
    </div>
  );
}

export function VarsPanel({ className, projectId, pipelineId, onInsert, onClose }: VarsPanelProps) {
  const [secrets, setSecrets] = React.useState<Secret[]>([]);
  const [envVars, setEnvVars] = React.useState<EnvVar[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [secretsOpen, setSecretsOpen] = React.useState(true);
  const [envVarsOpen, setEnvVarsOpen] = React.useState(true);
  const [builtinsOpen, setBuiltinsOpen] = React.useState(false);

  React.useEffect(() => {
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const results: { secrets: Secret[]; envVars: EnvVar[] } = {
          secrets: [],
          envVars: [],
        };

        // Load global secrets/env
        const [globalSecrets, globalEnv] = await Promise.all([
          secretsApi.list("global").catch(() => [] as Secret[]),
          envVarsApi.list("global").catch(() => [] as EnvVar[]),
        ]);
        results.secrets.push(...globalSecrets);
        results.envVars.push(...globalEnv);

        // Load project-scoped secrets/env
        if (projectId) {
          const [projSecrets, projEnv] = await Promise.all([
            secretsApi.list("project", projectId).catch(() => [] as Secret[]),
            envVarsApi.list("project", projectId).catch(() => [] as EnvVar[]),
          ]);
          results.secrets.push(...projSecrets);
          results.envVars.push(...projEnv);
        }

        // Load pipeline-scoped secrets/env
        if (pipelineId) {
          const [pipeSecrets, pipeEnv] = await Promise.all([
            secretsApi.list("pipeline", pipelineId).catch(() => [] as Secret[]),
            envVarsApi.list("pipeline", pipelineId).catch(() => [] as EnvVar[]),
          ]);
          results.secrets.push(...pipeSecrets);
          results.envVars.push(...pipeEnv);
        }

        // Deduplicate by name (later scopes override)
        const secretMap = new Map<string, Secret>();
        for (const s of results.secrets) secretMap.set(s.name, s);
        const envMap = new Map<string, EnvVar>();
        for (const e of results.envVars) envMap.set(e.name, e);

        setSecrets(Array.from(secretMap.values()));
        setEnvVars(Array.from(envMap.values()));
      } catch (err) {
        setError("Failed to load variables");
        console.error(err);
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [projectId, pipelineId]);

  const scopeBadge = (scopeType: string) => {
    switch (scopeType) {
      case "global":
        return { label: "Global", className: "bg-indigo-500/15 text-indigo-600 dark:text-indigo-400" };
      case "project":
        return { label: "Project", className: "bg-teal-500/15 text-teal-600 dark:text-teal-400" };
      case "pipeline":
        return { label: "Pipeline", className: "bg-orange-500/15 text-orange-600 dark:text-orange-400" };
      default:
        return { label: scopeType, className: "bg-muted text-muted-foreground" };
    }
  };

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between border-b px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
            <Variable className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-none">Variables & Secrets</h3>
            <p className="mt-0.5 text-[11px] text-muted-foreground">Pipeline variables</p>
          </div>
        </div>
        {onClose && (
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-foreground"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
            <span className="sr-only">Close</span>
          </Button>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : error ? (
        <div className="flex flex-col items-center gap-2 py-12 text-center">
          <AlertCircle className="h-5 w-5 text-destructive" />
          <p className="text-sm text-muted-foreground">{error}</p>
        </div>
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="divide-y">
            {/* Built-in Variables Section */}
            <div>
              <button
                type="button"
                onClick={() => setBuiltinsOpen((p) => !p)}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-muted/50 transition-colors"
              >
                {builtinsOpen ? (
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                )}
                <Braces className="h-4 w-4 shrink-0 text-emerald-500" />
                <span className="font-medium">
                  Built-in Variables
                  <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                    (auto-populated)
                  </span>
                </span>
              </button>
              {builtinsOpen && (
                <div className="px-4 pb-3">
                  <p className="mb-2.5 text-[11px] text-muted-foreground leading-relaxed">
                    These variables are automatically set at build time. Use them
                    in commands, image tags, notifications, and more.
                  </p>

                  {/* ── build.* ────────────────── */}
                  <h5 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Build
                  </h5>
                  <div className="space-y-2 mb-3">
                    {([
                      ["build.number", "Incremental build number (e.g. 42)"],
                      ["build.branch", "Git branch that triggered the build"],
                      ["build.commit", "Full Git commit SHA"],
                      ["build.id", "Unique build identifier (UUID)"],
                      ["build.status", "Current build status (running, success, failed)"],
                      ["build.trigger", "How the build was triggered (manual, push, schedule, webhook)"],
                      ["build.created_at", "ISO 8601 timestamp when the build was created"],
                      ["build.started_at", "ISO 8601 timestamp when the build started executing"],
                    ] as [string, string][]).map(([key, desc]) => (
                      <div key={key} className="space-y-0.5">
                        <CopySnippet snippet={`\${{ ${key} }}`} onInsert={onInsert} />
                        <p className="text-[10px] text-muted-foreground pl-0.5">{desc}</p>
                      </div>
                    ))}
                  </div>

                  {/* ── pipeline.* ─────────────── */}
                  <h5 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Pipeline
                  </h5>
                  <div className="space-y-2 mb-3">
                    {([
                      ["pipeline.name", "Name of the pipeline"],
                      ["pipeline.id", "Unique pipeline identifier (UUID)"],
                      ["pipeline.repo_url", "Source repository URL"],
                      ["pipeline.default_branch", "Default branch configured for this pipeline"],
                    ] as [string, string][]).map(([key, desc]) => (
                      <div key={key} className="space-y-0.5">
                        <CopySnippet snippet={`\${{ ${key} }}`} onInsert={onInsert} />
                        <p className="text-[10px] text-muted-foreground pl-0.5">{desc}</p>
                      </div>
                    ))}
                  </div>

                  {/* ── project.* ──────────────── */}
                  <h5 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Project
                  </h5>
                  <div className="space-y-2 mb-3">
                    {([
                      ["project.name", "Name of the project"],
                      ["project.slug", "URL-safe project slug"],
                      ["project.id", "Unique project identifier (UUID)"],
                    ] as [string, string][]).map(([key, desc]) => (
                      <div key={key} className="space-y-0.5">
                        <CopySnippet snippet={`\${{ ${key} }}`} onInsert={onInsert} />
                        <p className="text-[10px] text-muted-foreground pl-0.5">{desc}</p>
                      </div>
                    ))}
                  </div>

                  {/* ── megooci.* ──────────────── */}
                  <h5 className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    MegooCI
                  </h5>
                  <div className="space-y-2">
                    {([
                      ["megooci.url", "Public URL of this MegooCI instance"],
                    ] as [string, string][]).map(([key, desc]) => (
                      <div key={key} className="space-y-0.5">
                        <CopySnippet snippet={`\${{ ${key} }}`} onInsert={onInsert} />
                        <p className="text-[10px] text-muted-foreground pl-0.5">{desc}</p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

            {/* Secrets Section */}
            <div>
              <button
                type="button"
                onClick={() => setSecretsOpen((p) => !p)}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-muted/50 transition-colors"
              >
                {secretsOpen ? (
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                )}
                <Lock className="h-4 w-4 shrink-0 text-amber-500" />
                <span className="font-medium">
                  Secrets
                  <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                    ({secrets.length})
                  </span>
                </span>
              </button>
              {secretsOpen && (
                <div className="px-4 pb-3">
                  {secrets.length === 0 ? (
                    <p className="py-2 text-xs text-muted-foreground italic">
                      No secrets defined. Add secrets in Settings → Secrets & Variables.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {secrets.map((s) => (
                        <div key={s.id} className="space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium">{s.name}</span>
                            <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${scopeBadge(s.scope_type).className}`}>
                              {scopeBadge(s.scope_type).label}
                            </span>
                          </div>
                          <CopySnippet
                            snippet={`\${{ secrets.${s.name} }}`}
                            onInsert={onInsert}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Environment Variables Section */}
            <div>
              <button
                type="button"
                onClick={() => setEnvVarsOpen((p) => !p)}
                className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-muted/50 transition-colors"
              >
                {envVarsOpen ? (
                  <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                ) : (
                  <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                )}
                <Variable className="h-4 w-4 shrink-0 text-blue-500" />
                <span className="font-medium">
                  Environment Variables
                  <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                    ({envVars.length})
                  </span>
                </span>
              </button>
              {envVarsOpen && (
                <div className="px-4 pb-3">
                  {envVars.length === 0 ? (
                    <p className="py-2 text-xs text-muted-foreground italic">
                      No environment variables defined. Add them in Settings → Secrets & Variables.
                    </p>
                  ) : (
                    <div className="space-y-2">
                      {envVars.map((e) => (
                        <div key={e.id} className="space-y-1">
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium">{e.name}</span>
                            <div className="flex items-center gap-1.5">
                              {e.is_secret_ref && (
                                <span title="References a secret">
                                  <Lock className="h-3 w-3 text-amber-500" />
                                </span>
                              )}
                              <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${scopeBadge(e.scope_type).className}`}>
                                {scopeBadge(e.scope_type).label}
                              </span>
                            </div>
                          </div>
                          <CopySnippet
                            snippet={`\${{ env.${e.name} }}`}
                            onInsert={onInsert}
                          />
                          {!e.is_secret_ref && e.value && (
                            <p className="text-[10px] text-muted-foreground truncate" title={e.value}>
                              Value: {e.value}
                            </p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Syntax Help */}
            <div className="px-4 py-3">
              <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Syntax Reference
              </h4>
              <div className="space-y-1.5 text-xs text-muted-foreground">
                <p>Use these placeholders anywhere in your pipeline YAML:</p>
                <div className="rounded-md bg-muted/50 border p-2 font-mono text-[11px] space-y-1">
                  <p>{"${{ secrets.SECRET_NAME }}"}</p>
                  <p>{"${{ env.VAR_NAME }}"}</p>
                  <p>{"${{ build.branch }}"}</p>
                  <p>{"${{ pipeline.name }}"}</p>
                  <p>{"${{ project.slug }}"}</p>
                </div>
                <p className="text-[11px] leading-relaxed">
                  Secrets are decrypted at runtime and masked in logs.
                  Environment variables from narrower scopes (pipeline → project → global) override broader ones.
                  Built-in variables are populated automatically at build time.
                </p>
              </div>
            </div>
          </div>
        </ScrollArea>
      )}
    </div>
  );
}
