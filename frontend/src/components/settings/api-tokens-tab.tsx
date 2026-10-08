"use client";

import * as React from "react";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import { KeyRound, Plus, Copy, Trash2 } from "lucide-react";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { apiTokensApi, type SystemInfo, type ApiTokenCreated, type ApiTokenScope, type ApiToken } from "@/lib/api";
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
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

function mcpAddCommand(url: string): string {
  // --scope user: available in all of the user's projects, not only the
  // directory where the command is run.
  return `claude mcp add --transport http --scope user megooci ${url} --header "Authorization: Bearer <your-token>"`;
}

export function ApiTokensTab({ info }: { info: SystemInfo | null }) {
  const confirm = useConfirm();

  // API Tokens state
  const [tokens, setTokens] = React.useState<ApiToken[]>([]);
  const [tokensLoading, setTokensLoading] = React.useState(true);
  const [showCreateToken, setShowCreateToken] = React.useState(false);
  const [newTokenName, setNewTokenName] = React.useState("");
  const [newTokenExpiry, setNewTokenExpiry] = React.useState<string>("");
  const [creatingToken, setCreatingToken] = React.useState(false);
  const [createdToken, setCreatedToken] = React.useState<ApiTokenCreated | null>(null);
  const [scopeCatalog, setScopeCatalog] = React.useState<ApiTokenScope[]>([]);
  const [newTokenScope, setNewTokenScope] = React.useState<string>("full_access");

  // Loaded when the tab is first opened.
  React.useEffect(() => {
    apiTokensApi
      .list()
      .then(setTokens)
      .catch(() => {})
      .finally(() => setTokensLoading(false));

    apiTokensApi
      .scopes()
      .then(setScopeCatalog)
      .catch(() => {});
  }, []);

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                <KeyRound className="h-5 w-5" />
                API Tokens
              </CardTitle>
              <CardDescription>
                Personal access tokens for API authentication. Use these to
                authenticate with the MegooCI API, download artifacts, and
                automate workflows.
              </CardDescription>
            </div>
            <Button
              size="sm"
              onClick={() => setShowCreateToken(true)}
              className="gap-1.5"
            >
              <Plus className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Create token</span>
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {tokensLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 2 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : tokens.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              No API tokens yet. Create one to authenticate scripts and CI/CD
              integrations.
            </p>
          ) : (
            <div className="space-y-2">
              {tokens.map((t) => (
                <div
                  key={t.id}
                  className="flex items-center justify-between rounded-lg border px-3 py-3"
                >
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-medium">{t.name}</span>
                      <Badge
                        variant={t.is_active ? "success" : "cancelled"}
                        className="text-[10px]"
                      >
                        {t.is_active ? "Active" : "Revoked"}
                      </Badge>
                      <Badge variant="outline" className="text-[10px]">
                        {t.scope.label}
                      </Badge>
                    </div>
                    <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                      <span>
                        <code>{t.token_hint}…</code>
                      </span>
                      {t.expires_at && (
                        <span>
                          Expires{" "}
                          {formatDistanceToNow(new Date(t.expires_at), {
                            addSuffix: true,
                          })}
                        </span>
                      )}
                      {t.last_used_at && (
                        <span>
                          Last used{" "}
                          {formatDistanceToNow(new Date(t.last_used_at), {
                            addSuffix: true,
                          })}
                        </span>
                      )}
                      <span>
                        Created{" "}
                        {formatDistanceToNow(new Date(t.created_at), {
                          addSuffix: true,
                        })}
                      </span>
                    </div>
                  </div>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="h-8 w-8 text-destructive"
                    onClick={async () => {
                      const ok = await confirm({
                        title: "Delete this token?",
                        description: (
                          <>
                            Token <strong>{t.name}</strong> (
                            <code>{t.token_hint}…</code>) will be
                            permanently removed. Any scripts using it
                            will stop working.
                          </>
                        ),
                        confirmText: "Delete",
                        tone: "destructive",
                      });
                      if (!ok) return;
                      try {
                        await apiTokensApi.remove(t.id);
                        setTokens((prev) =>
                          prev.filter((x) => x.id !== t.id),
                        );
                        toast.success("Token deleted");
                      } catch {
                        toast.error("Failed to delete token");
                      }
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              ))}
            </div>
          )}
          {info?.mcp?.enabled && (
            <div className="mt-4 space-y-2 rounded-lg border bg-muted/30 p-3">
              <p className="text-sm font-medium">Connect a coding agent (MCP)</p>
              <p className="text-xs text-muted-foreground">
                Coding agents such as Claude Code can work with your projects,
                pipelines and builds through MCP, using one of your API
                tokens. Create a token with the{" "}
                <strong>Coding agent</strong> scope and put it in place of{" "}
                <code>&lt;your-token&gt;</code>.
              </p>
              <div className="flex items-center gap-2">
                <Input
                  readOnly
                  value={info.mcp.url}
                  aria-label="MCP server URL"
                  className="font-mono text-xs"
                />
                <Button
                  variant="outline"
                  size="icon"
                  className="shrink-0"
                  aria-label="Copy MCP server URL"
                  onClick={() => {
                    navigator.clipboard.writeText(info.mcp.url);
                    toast.success("Copied to clipboard");
                  }}
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
              <div className="flex items-start gap-2">
                <code className="block flex-1 break-all rounded bg-muted px-2 py-1 text-xs">
                  {mcpAddCommand(info.mcp.url)}
                </code>
                <Button
                  variant="outline"
                  size="icon"
                  className="shrink-0"
                  aria-label="Copy setup command"
                  onClick={() => {
                    navigator.clipboard.writeText(mcpAddCommand(info.mcp.url));
                    toast.success("Copied to clipboard");
                  }}
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Create Token Dialog */}
      <Dialog
        open={showCreateToken || !!createdToken}
        onOpenChange={(open) => {
          if (!open) {
            setShowCreateToken(false);
            setCreatedToken(null);
            setNewTokenName("");
            setNewTokenExpiry("");
            setNewTokenScope("full_access");
          }
        }}
      >
        <DialogContent>
          {createdToken ? (
            <>
              <DialogHeader>
                <DialogTitle>Token created</DialogTitle>
                <DialogDescription>
                  Copy this token now — you won&apos;t be able to see it again.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <Input
                    readOnly
                    value={createdToken.token}
                    className="font-mono text-xs"
                  />
                  <Button
                    variant="outline"
                    size="icon"
                    className="shrink-0"
                    onClick={() => {
                      navigator.clipboard.writeText(createdToken.token);
                      toast.success("Copied to clipboard");
                    }}
                  >
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Use this token as a Bearer token in the{" "}
                  <code>Authorization</code> header, e.g.:{" "}
                  <code className="block mt-1 break-all rounded bg-muted px-2 py-1">
                    curl -H &quot;Authorization: Bearer {createdToken.token_hint}…&quot;
                  </code>
                </p>
              </div>
              <DialogFooter>
                <Button
                  onClick={() => {
                    setCreatedToken(null);
                    setShowCreateToken(false);
                  }}
                >
                  Done
                </Button>
              </DialogFooter>
            </>
          ) : (
            <>
              <DialogHeader>
                <DialogTitle>Create API token</DialogTitle>
                <DialogDescription>
                  Choose what this token can do. Access is always capped by
                  your role&apos;s permissions.
                </DialogDescription>
              </DialogHeader>
              <form
                onSubmit={async (e) => {
                  e.preventDefault();
                  if (!newTokenName.trim()) {
                    toast.error("Token name is required");
                    return;
                  }
                  setCreatingToken(true);
                  try {
                    const result = await apiTokensApi.create({
                      name: newTokenName.trim(),
                      expires_in_days: newTokenExpiry
                        ? parseInt(newTokenExpiry, 10)
                        : null,
                      scope: newTokenScope,
                    });
                    setCreatedToken(result);
                    setTokens((prev) => [
                      {
                        id: result.id,
                        name: result.name,
                        token_hint: result.token_hint,
                        scopes: result.scopes,
                        scope: result.scope,
                        expires_at: result.expires_at,
                        is_active: result.is_active,
                        last_used_at: result.last_used_at,
                        created_at: result.created_at,
                      },
                      ...prev,
                    ]);
                    setNewTokenName("");
                    setNewTokenExpiry("");
                    setNewTokenScope("full_access");
                  } catch {
                    toast.error("Failed to create token");
                  } finally {
                    setCreatingToken(false);
                  }
                }}
                className="space-y-4"
              >
                <div className="space-y-2">
                  <label className="text-sm font-medium">Token name</label>
                  <Input
                    placeholder="e.g. CI deploy script"
                    value={newTokenName}
                    onChange={(e) => setNewTokenName(e.target.value)}
                    autoFocus
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Scope</label>
                  <select
                    value={newTokenScope}
                    onChange={(e) => setNewTokenScope(e.target.value)}
                    className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                  >
                    {scopeCatalog.length === 0 ? (
                      <option value="full_access">Full access</option>
                    ) : (
                      scopeCatalog.map((s) => (
                        <option key={s.key} value={s.key}>
                          {s.label}
                        </option>
                      ))
                    )}
                  </select>
                  <p className="text-xs text-muted-foreground">
                    {scopeCatalog.find((s) => s.key === newTokenScope)?.description}
                  </p>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">
                    Expires in (days)
                  </label>
                  <Input
                    type="number"
                    min={1}
                    max={365}
                    placeholder="Leave empty for no expiry"
                    value={newTokenExpiry}
                    onChange={(e) => setNewTokenExpiry(e.target.value)}
                  />
                </div>
                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setShowCreateToken(false);
                      setNewTokenName("");
                      setNewTokenExpiry("");
                      setNewTokenScope("full_access");
                    }}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={creatingToken}>
                    {creatingToken ? "Creating…" : "Create token"}
                  </Button>
                </DialogFooter>
              </form>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
}
