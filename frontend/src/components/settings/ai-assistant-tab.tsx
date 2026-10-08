"use client";

import * as React from "react";
import { toast } from "sonner";
import { Sparkles, CheckCircle2, AlertTriangle, XCircle, KeyRound, Pencil, Save, RotateCcw, Eye, EyeOff } from "lucide-react";
import { systemApi, type AiInfo, type SystemInfo, type AiSettingsUpdate } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ConfigRow } from "@/components/settings/config-row";

function AiStatusBadge({ ai }: { ai: AiInfo }) {
  if (ai.status === "ready") {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" /> Ready
      </Badge>
    );
  }
  if (ai.status === "disabled") {
    return (
      <Badge variant="cancelled" className="gap-1">
        <XCircle className="h-3 w-3" /> Disabled
      </Badge>
    );
  }
  if (ai.status === "missing_api_key") {
    return (
      <Badge variant="failed" className="gap-1">
        <KeyRound className="h-3 w-3" /> Missing API key
      </Badge>
    );
  }
  return (
    <Badge variant="failed" className="gap-1">
      <AlertTriangle className="h-3 w-3" /> Misconfigured
    </Badge>
  );
}

const AI_PROVIDERS = [
  { value: "openai", label: "OpenAI" },
  { value: "anthropic", label: "Anthropic" },
  { value: "ollama", label: "Ollama" },
  { value: "azure_openai", label: "Azure OpenAI" },
  { value: "custom", label: "Custom (OpenAI-compatible)" },
  { value: "disabled", label: "Disabled" },
];

export function AiConfigCard({
  info,
  loading,
  isAdmin,
  onUpdated,
}: {
  info: SystemInfo | null;
  loading: boolean;
  isAdmin: boolean;
  onUpdated: (ai: AiInfo) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  const [saving, setSaving] = React.useState(false);
  const [showKey, setShowKey] = React.useState(false);

  // Form state
  const [provider, setProvider] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [model, setModel] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [enabled, setEnabled] = React.useState(true);

  // Sync form state when entering edit mode or when info changes
  function syncForm() {
    if (!info) return;
    setProvider(info.ai.provider || "openai");
    setBaseUrl(info.ai.base_url || "");
    setModel(info.ai.model || "");
    setApiKey(""); // never pre-fill API key for security
    setEnabled(info.ai.enabled);
    setShowKey(false);
  }

  function handleEdit() {
    syncForm();
    setEditing(true);
  }

  function handleCancel() {
    setEditing(false);
    setShowKey(false);
  }

  async function handleSave() {
    setSaving(true);
    try {
      const update: AiSettingsUpdate = {
        enabled,
        provider,
        base_url: baseUrl,
        model,
      };
      // Only send api_key if user actually typed something
      if (apiKey) {
        update.api_key = apiKey;
      }
      const updatedAi = await systemApi.updateAi(update);
      onUpdated(updatedAi);
      setEditing(false);
      setShowKey(false);
      toast.success("AI configuration updated");
    } catch (err: unknown) {
      const detail =
        (err as { body?: { detail?: string } })?.body?.detail ??
        (err instanceof Error ? err.message : "Failed to update AI settings");
      toast.error(detail);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              AI Configuration
            </CardTitle>
            <CardDescription>
              {editing
                ? "Configure the LLM provider for the AI pipeline assistant."
                : "Current AI provider configuration."}
            </CardDescription>
          </div>
          {isAdmin && !editing && !loading && info && (
            <Button
              variant="outline"
              size="sm"
              className="gap-1.5"
              onClick={handleEdit}
            >
              <Pencil className="h-3.5 w-3.5" />
              Edit
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {loading || !info ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : editing ? (
          /* ─── Edit Mode ─── */
          <div className="space-y-4">
            {/* Enabled */}
            <div className="flex items-center justify-between rounded-lg border px-3 py-3">
              <label className="text-sm text-muted-foreground">Enabled</label>
              <button
                type="button"
                role="switch"
                aria-checked={enabled}
                onClick={() => setEnabled(!enabled)}
                className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                  enabled ? "bg-primary" : "bg-muted"
                }`}
              >
                <span
                  className={`pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                    enabled ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>

            {/* Provider */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Provider</label>
              <select
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                {AI_PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value}>
                    {p.label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-muted-foreground">
                Select &ldquo;Custom&rdquo; for any OpenAI-compatible API (vLLM,
                LiteLLM, LM Studio, etc.)
              </p>
            </div>

            {/* Base URL */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Base URL</label>
              <Input
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                placeholder={
                  provider === "ollama"
                    ? "http://localhost:11434/v1"
                    : provider === "openai"
                      ? "https://api.openai.com/v1 (default)"
                      : "https://your-api-endpoint/v1"
                }
              />
              <p className="text-xs text-muted-foreground">
                Leave empty for the provider&apos;s default endpoint. Set this to
                point to a different OpenAI-compatible server.
              </p>
            </div>

            {/* Model */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">Model</label>
              <Input
                value={model}
                onChange={(e) => setModel(e.target.value)}
                placeholder={
                  provider === "openai"
                    ? "gpt-4o-mini"
                    : provider === "anthropic"
                      ? "claude-sonnet-4-5"
                      : provider === "ollama"
                        ? "llama3.2"
                        : "model-name"
                }
              />
            </div>

            {/* API Key */}
            <div className="space-y-1.5">
              <label className="text-sm font-medium">API Key</label>
              <div className="flex gap-2">
                <div className="relative flex-1">
                  <Input
                    type={showKey ? "text" : "password"}
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder={
                      info.ai.has_api_key
                        ? "••••••••  (leave empty to keep current)"
                        : "sk-..."
                    }
                    autoComplete="off"
                  />
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9 shrink-0"
                  onClick={() => setShowKey(!showKey)}
                >
                  {showKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                {info.ai.has_api_key
                  ? "An API key is already set. Leave this empty to keep it, or enter a new one to replace it."
                  : "Required for cloud providers (OpenAI, Anthropic, Azure). Not needed for local Ollama."}
              </p>
            </div>

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                onClick={handleCancel}
                disabled={saving}
              >
                <RotateCcw className="mr-1.5 h-3.5 w-3.5" />
                Cancel
              </Button>
              <Button onClick={handleSave} disabled={saving}>
                <Save className="mr-1.5 h-3.5 w-3.5" />
                {saving ? "Saving…" : "Save changes"}
              </Button>
            </div>
          </div>
        ) : (
          /* ─── Read-only Mode ─── */
          <>
            <div className="space-y-3">
              <ConfigRow label="Status">
                <AiStatusBadge ai={info.ai} />
              </ConfigRow>
              <ConfigRow label="Enabled">
                <Badge
                  variant={info.ai.enabled ? "success" : "cancelled"}
                >
                  {info.ai.enabled ? "Yes" : "No"}
                </Badge>
              </ConfigRow>
              <ConfigRow label="Provider">
                <code className="rounded bg-muted px-2 py-0.5 text-xs">
                  {AI_PROVIDERS.find((p) => p.value === info.ai.provider)?.label || info.ai.provider || "—"}
                </code>
              </ConfigRow>
              <ConfigRow label="Model">
                <code className="rounded bg-muted px-2 py-0.5 text-xs">
                  {info.ai.model || "—"}
                </code>
              </ConfigRow>
              <ConfigRow label="API Key">
                <Badge
                  variant={info.ai.has_api_key ? "success" : "cancelled"}
                >
                  {info.ai.has_api_key ? "Set" : "Not set"}
                </Badge>
              </ConfigRow>
              {info.ai.base_url && (
                <ConfigRow label="Base URL">
                  <code className="rounded bg-muted px-2 py-0.5 text-xs">
                    {info.ai.base_url}
                  </code>
                </ConfigRow>
              )}
            </div>
            <p
              className={`mt-4 text-xs ${
                info.ai.status === "ready"
                  ? "text-muted-foreground"
                  : "text-amber-600 dark:text-amber-400"
              }`}
            >
              {info.ai.status_detail}
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
