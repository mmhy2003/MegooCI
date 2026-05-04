"use client";

import * as React from "react";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import { Eye, EyeOff, Globe, KeyRound, Pencil, Plus, Trash2, Variable } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { usePermission } from "@/hooks/use-permission";
import {
  projectsApi,
  secretsApi,
  envVarsApi,
  type Project,
  type Secret,
  type EnvVar,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const GLOBAL_SCOPE = "__global__";

interface FlatSecret extends Secret {
  projectName: string;
}

interface FlatEnvVar extends EnvVar {
  projectName: string;
}

export default function SecretsPage() {
  const confirm = useConfirm();
  const canManage = usePermission("secrets.manage");
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [allSecrets, setAllSecrets] = React.useState<FlatSecret[]>([]);
  const [allEnvVars, setAllEnvVars] = React.useState<FlatEnvVar[]>([]);
  const [loading, setLoading] = React.useState(true);

  // Add Secret dialog
  const [secretDialogOpen, setSecretDialogOpen] = React.useState(false);
  const [secProjectId, setSecProjectId] = React.useState(GLOBAL_SCOPE);
  const [secName, setSecName] = React.useState("");
  const [secValue, setSecValue] = React.useState("");
  const [creatingSec, setCreatingSec] = React.useState(false);
  const [showSecValue, setShowSecValue] = React.useState(false);

  // Edit Secret dialog
  const [editSecretDialogOpen, setEditSecretDialogOpen] = React.useState(false);
  const [editSecretId, setEditSecretId] = React.useState("");
  const [editSecName, setEditSecName] = React.useState("");
  const [editSecValue, setEditSecValue] = React.useState("");
  const [editSecScope, setEditSecScope] = React.useState(GLOBAL_SCOPE);
  const [showEditSecValue, setShowEditSecValue] = React.useState(false);
  const [savingSec, setSavingSec] = React.useState(false);

  // Add Env Var dialog
  const [envDialogOpen, setEnvDialogOpen] = React.useState(false);
  const [envProjectId, setEnvProjectId] = React.useState(GLOBAL_SCOPE);
  const [envName, setEnvName] = React.useState("");
  const [envValue, setEnvValue] = React.useState("");
  const [creatingEnv, setCreatingEnv] = React.useState(false);

  // Edit Env Var dialog
  const [editEnvDialogOpen, setEditEnvDialogOpen] = React.useState(false);
  const [editEnvId, setEditEnvId] = React.useState("");
  const [editEnvName, setEditEnvName] = React.useState("");
  const [editEnvValue, setEditEnvValue] = React.useState("");
  const [editEnvScope, setEditEnvScope] = React.useState(GLOBAL_SCOPE);
  const [savingEnv, setSavingEnv] = React.useState(false);

  async function loadData() {
    try {
      const allProjects = await projectsApi.list();
      setProjects(allProjects);

      // Fetch global + per-project secrets and env vars in parallel.
      const [globalSecs, globalVars, ...perProject] = await Promise.all([
        secretsApi.list("global").catch(() => [] as Secret[]),
        envVarsApi.list("global").catch(() => [] as EnvVar[]),
        ...allProjects.flatMap((project) => [
          secretsApi.list("project", project.id).catch(() => [] as Secret[]),
          envVarsApi.list("project", project.id).catch(() => [] as EnvVar[]),
        ]),
      ]);

      // Build flat lists with project name annotations.
      const secrets: FlatSecret[] = [
        ...globalSecs.map((s) => ({ ...s, projectName: "Global" })),
      ];
      const envVars: FlatEnvVar[] = [
        ...globalVars.map((v) => ({ ...v, projectName: "Global" })),
      ];

      for (let i = 0; i < allProjects.length; i++) {
        const project = allProjects[i];
        const projSecrets = perProject[i * 2] as Secret[];
        const projVars = perProject[i * 2 + 1] as EnvVar[];
        secrets.push(...projSecrets.map((s) => ({ ...s, projectName: project.name })));
        envVars.push(...projVars.map((v) => ({ ...v, projectName: project.name })));
      }

      setAllSecrets(secrets);
      setAllEnvVars(envVars);
    } catch {
      toast.error("Failed to load secrets data");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => {
    loadData();
  }, []);

  // ── Secret handlers ────────────────────────────────────────────────

  async function handleAddSecret(e: React.FormEvent) {
    e.preventDefault();
    if (!secName.trim() || !secValue.trim()) {
      toast.error("Name and value are required");
      return;
    }
    setCreatingSec(true);
    try {
      const isGlobal = secProjectId === GLOBAL_SCOPE;
      await secretsApi.create({
        scope_type: isGlobal ? "global" : "project",
        ...(isGlobal ? {} : { scope_id: secProjectId }),
        name: secName,
        value: secValue,
      });
      setSecretDialogOpen(false);
      setSecName("");
      setSecValue("");
      setShowSecValue(false);
      toast.success("Secret added");
      loadData();
    } catch {
      toast.error("Failed to add secret");
    } finally {
      setCreatingSec(false);
    }
  }

  function openEditSecret(secret: FlatSecret) {
    setEditSecretId(secret.id);
    setEditSecName(secret.name);
    setEditSecValue("");
    setEditSecScope(secret.scope_id ?? GLOBAL_SCOPE);
    setShowEditSecValue(false);
    setEditSecretDialogOpen(true);
  }

  async function handleEditSecret(e: React.FormEvent) {
    e.preventDefault();
    const updates: { name?: string; value?: string; scope_type?: string; scope_id?: string | null } = {};
    const original = allSecrets.find((s) => s.id === editSecretId);
    if (editSecName.trim() && editSecName !== original?.name) {
      updates.name = editSecName.trim();
    }
    if (editSecValue.trim()) {
      updates.value = editSecValue;
    }
    // Check if scope changed.
    const origScope = original?.scope_id ?? GLOBAL_SCOPE;
    if (editSecScope !== origScope) {
      const isGlobal = editSecScope === GLOBAL_SCOPE;
      updates.scope_type = isGlobal ? "global" : "project";
      updates.scope_id = isGlobal ? null : editSecScope;
    }
    if (Object.keys(updates).length === 0) {
      setEditSecretDialogOpen(false);
      return;
    }
    setSavingSec(true);
    try {
      await secretsApi.update(editSecretId, updates);
      setEditSecretDialogOpen(false);
      toast.success("Secret updated");
      loadData();
    } catch {
      toast.error("Failed to update secret");
    } finally {
      setSavingSec(false);
    }
  }

  async function handleDeleteSecret(secretId: string) {
    const secret = allSecrets.find((s) => s.id === secretId);
    const ok = await confirm({
      title: "Delete this secret?",
      description: (
        <>
          <code className="font-mono text-foreground">
            {secret?.name ?? "This secret"}
          </code>{" "}
          will be permanently removed
          {secret?.projectName === "Global"
            ? " from the global scope. All projects using it will be affected."
            : (
                <>
                  {" "}from{" "}
                  <span className="font-medium text-foreground">
                    {secret?.projectName ?? "the project"}
                  </span>
                  . Pipelines using it will fail until it&apos;s recreated.
                </>
              )}
        </>
      ),
      confirmText: "Delete secret",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await secretsApi.delete(secretId);
      toast.success("Secret deleted");
      loadData();
    } catch {
      toast.error("Failed to delete secret");
    }
  }

  // ── Env var handlers ───────────────────────────────────────────────

  async function handleAddEnv(e: React.FormEvent) {
    e.preventDefault();
    if (!envName.trim() || !envValue.trim()) {
      toast.error("Name and value are required");
      return;
    }
    setCreatingEnv(true);
    try {
      const isGlobal = envProjectId === GLOBAL_SCOPE;
      await envVarsApi.create({
        scope_type: isGlobal ? "global" : "project",
        ...(isGlobal ? {} : { scope_id: envProjectId }),
        name: envName,
        value: envValue,
      });
      setEnvDialogOpen(false);
      setEnvName("");
      setEnvValue("");
      toast.success("Variable added");
      loadData();
    } catch {
      toast.error("Failed to add variable");
    } finally {
      setCreatingEnv(false);
    }
  }

  function openEditEnv(envVar: FlatEnvVar) {
    setEditEnvId(envVar.id);
    setEditEnvName(envVar.name);
    setEditEnvValue(envVar.value);
    setEditEnvScope(envVar.scope_id ?? GLOBAL_SCOPE);
    setEditEnvDialogOpen(true);
  }

  async function handleEditEnv(e: React.FormEvent) {
    e.preventDefault();
    if (!editEnvValue.trim()) {
      toast.error("Value is required");
      return;
    }
    const updates: { value?: string; scope_type?: string; scope_id?: string | null } = {};
    const original = allEnvVars.find((v) => v.id === editEnvId);
    if (editEnvValue.trim() !== original?.value) {
      updates.value = editEnvValue.trim();
    }
    // Check if scope changed.
    const origScope = original?.scope_id ?? GLOBAL_SCOPE;
    if (editEnvScope !== origScope) {
      const isGlobal = editEnvScope === GLOBAL_SCOPE;
      updates.scope_type = isGlobal ? "global" : "project";
      updates.scope_id = isGlobal ? null : editEnvScope;
    }
    if (Object.keys(updates).length === 0) {
      setEditEnvDialogOpen(false);
      return;
    }
    setSavingEnv(true);
    try {
      await envVarsApi.update(editEnvId, updates);
      setEditEnvDialogOpen(false);
      toast.success("Variable updated");
      loadData();
    } catch {
      toast.error("Failed to update variable");
    } finally {
      setSavingEnv(false);
    }
  }

  async function handleDeleteEnv(envId: string) {
    const envVar = allEnvVars.find((v) => v.id === envId);
    const ok = await confirm({
      title: "Delete this variable?",
      description: (
        <>
          <code className="font-mono text-foreground">
            {envVar?.name ?? "This variable"}
          </code>{" "}
          will be removed
          {envVar?.projectName === "Global"
            ? " from the global scope."
            : (
                <>
                  {" "}from{" "}
                  <span className="font-medium text-foreground">
                    {envVar?.projectName ?? "the project"}
                  </span>
                  .
                </>
              )}
        </>
      ),
      confirmText: "Delete variable",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await envVarsApi.delete(envId);
      toast.success("Variable deleted");
      loadData();
    } catch {
      toast.error("Failed to delete variable");
    }
  }

  const scopeOptions = [
    { value: GLOBAL_SCOPE, label: "🌐 Global (all projects)" },
    ...projects.map((p) => ({ value: p.id, label: p.name })),
  ];

  return (
    <AppLayout>
      <div className="space-y-8">
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            Secrets &amp; Variables
          </h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            Manage secrets and environment variables. Global items are available
            to every project; project-scoped items are only visible to that
            project&apos;s pipelines.
          </p>
        </div>

        {/* ── Secrets Section ─────────────────────────────────────────── */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
            <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
              <KeyRound className="h-5 w-5" />
              Secrets
            </CardTitle>
            <Dialog open={secretDialogOpen} onOpenChange={setSecretDialogOpen}>
              {canManage && (
                <Button
                  size="sm"
                  onClick={() => setSecretDialogOpen(true)}
                >
                  <Plus className="mr-1.5 h-4 w-4" />
                  Add Secret
                </Button>
              )}
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Add Secret</DialogTitle>
                  <DialogDescription>
                    Secrets are encrypted and cannot be viewed after creation.
                    Choose &quot;Global&quot; to make it available to all projects.
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleAddSecret} className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Scope</label>
                    <Select
                      value={secProjectId}
                      onChange={(e) => setSecProjectId(e.target.value)}
                      options={scopeOptions}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Name</label>
                    <Input
                      placeholder="SECRET_NAME"
                      value={secName}
                      onChange={(e) => setSecName(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Value</label>
                    <div className="relative">
                      <Textarea
                        placeholder="Paste secret value (supports multiline, e.g. SSH keys)"
                        value={secValue}
                        onChange={(e) => setSecValue(e.target.value)}
                        rows={4}
                        className={`resize-y pr-10 font-mono text-xs ${!showSecValue ? "text-security-disc" : ""}`}
                        style={!showSecValue ? { WebkitTextSecurity: "disc", textSecurity: "disc" } as React.CSSProperties : undefined}
                      />
                      <button
                        type="button"
                        onClick={() => setShowSecValue((v) => !v)}
                        className="absolute right-0 top-0 flex items-center px-3 py-2 text-muted-foreground hover:text-foreground transition-colors"
                        tabIndex={-1}
                      >
                        {showSecValue ? (
                          <EyeOff className="h-4 w-4" />
                        ) : (
                          <Eye className="h-4 w-4" />
                        )}
                        <span className="sr-only">
                          {showSecValue ? "Hide value" : "Show value"}
                        </span>
                      </button>
                    </div>
                  </div>
                  <DialogFooter>
                    <Button type="submit" disabled={creatingSec}>
                      {creatingSec ? "Adding…" : "Add Secret"}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : allSecrets.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No secrets configured. Add your first secret to get started.
              </p>
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="w-full min-w-[480px] text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-3 pr-4 font-medium">Name</th>
                      <th className="pb-3 pr-4 font-medium">Scope</th>
                      <th className="hidden pb-3 pr-4 font-medium sm:table-cell">
                        Created
                      </th>
                      <th className="pb-3 font-medium w-20"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {allSecrets.map((secret) => (
                      <tr key={secret.id} className="border-b last:border-0">
                        <td className="py-3 pr-4">
                          <code className="break-all font-medium">
                            {secret.name}
                          </code>
                        </td>
                        <td className="py-3 pr-4">
                          {secret.projectName === "Global" ? (
                            <Badge variant="secondary" className="gap-1">
                              <Globe className="h-3 w-3" />
                              Global
                            </Badge>
                          ) : (
                            <span className="text-muted-foreground">
                              {secret.projectName}
                            </span>
                          )}
                        </td>
                        <td className="hidden py-3 pr-4 text-muted-foreground sm:table-cell">
                          {formatDistanceToNow(new Date(secret.created_at), {
                            addSuffix: true,
                          })}
                        </td>
                        {canManage && (
                          <td className="py-3">
                            <div className="flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={() => openEditSecret(secret)}
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-destructive"
                                onClick={() => handleDeleteSecret(secret.id)}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Edit Secret Dialog */}
        <Dialog open={editSecretDialogOpen} onOpenChange={setEditSecretDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Edit Secret</DialogTitle>
              <DialogDescription>
                Update the name or replace the encrypted value.
                Leave the value blank to keep the existing one.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleEditSecret} className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Scope</label>
                <Select
                  value={editSecScope}
                  onChange={(e) => setEditSecScope(e.target.value)}
                  options={scopeOptions}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Name</label>
                <Input
                  placeholder="SECRET_NAME"
                  value={editSecName}
                  onChange={(e) => setEditSecName(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">
                  New value{" "}
                  <span className="text-muted-foreground">(optional)</span>
                </label>
                <div className="relative">
                  <Textarea
                    placeholder="Leave blank to keep current value (supports multiline)"
                    value={editSecValue}
                    onChange={(e) => setEditSecValue(e.target.value)}
                    rows={4}
                    className={`resize-y pr-10 font-mono text-xs ${!showEditSecValue ? "text-security-disc" : ""}`}
                    style={!showEditSecValue ? { WebkitTextSecurity: "disc", textSecurity: "disc" } as React.CSSProperties : undefined}
                  />
                  <button
                    type="button"
                    onClick={() => setShowEditSecValue((v) => !v)}
                    className="absolute right-0 top-0 flex items-center px-3 py-2 text-muted-foreground hover:text-foreground transition-colors"
                    tabIndex={-1}
                  >
                    {showEditSecValue ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
                    <span className="sr-only">
                      {showEditSecValue ? "Hide value" : "Show value"}
                    </span>
                  </button>
                </div>
              </div>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setEditSecretDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={savingSec}>
                  {savingSec ? "Saving…" : "Save Changes"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        {/* ── Environment Variables Section ───────────────────────────── */}
        <Card>
          <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
            <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
              <Variable className="h-5 w-5" />
              Environment Variables
            </CardTitle>
            <Dialog open={envDialogOpen} onOpenChange={setEnvDialogOpen}>
              {canManage && (
                <Button
                  size="sm"
                  onClick={() => setEnvDialogOpen(true)}
                >
                  <Plus className="mr-1.5 h-4 w-4" />
                  Add Variable
                </Button>
              )}
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Add Environment Variable</DialogTitle>
                  <DialogDescription>
                    Choose &quot;Global&quot; to make this variable available to all projects.
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleAddEnv} className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Scope</label>
                    <Select
                      value={envProjectId}
                      onChange={(e) => setEnvProjectId(e.target.value)}
                      options={scopeOptions}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Name</label>
                    <Input
                      placeholder="ENV_NAME"
                      value={envName}
                      onChange={(e) => setEnvName(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Value</label>
                    <Input
                      placeholder="value"
                      value={envValue}
                      onChange={(e) => setEnvValue(e.target.value)}
                    />
                  </div>
                  <DialogFooter>
                    <Button type="submit" disabled={creatingEnv}>
                      {creatingEnv ? "Adding…" : "Add Variable"}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <Skeleton key={i} className="h-10 w-full" />
                ))}
              </div>
            ) : allEnvVars.length === 0 ? (
              <p className="py-8 text-center text-sm text-muted-foreground">
                No environment variables configured.
              </p>
            ) : (
              <div className="-mx-2 overflow-x-auto px-2">
                <table className="w-full min-w-[420px] text-sm">
                  <thead>
                    <tr className="border-b text-left text-muted-foreground">
                      <th className="pb-3 pr-4 font-medium">Name</th>
                      <th className="hidden pb-3 pr-4 font-medium sm:table-cell">
                        Value
                      </th>
                      <th className="pb-3 pr-4 font-medium">Scope</th>
                      <th className="pb-3 font-medium w-20"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {allEnvVars.map((v) => (
                      <tr key={v.id} className="border-b last:border-0">
                        <td className="py-3 pr-4">
                          <code className="break-all font-medium">
                            {v.name}
                          </code>
                        </td>
                        <td className="hidden py-3 pr-4 text-muted-foreground sm:table-cell">
                          <span className="block max-w-[200px] truncate">
                            {v.is_secret_ref ? "••••••" : v.value}
                          </span>
                        </td>
                        <td className="py-3 pr-4">
                          {v.projectName === "Global" ? (
                            <Badge variant="secondary" className="gap-1">
                              <Globe className="h-3 w-3" />
                              Global
                            </Badge>
                          ) : (
                            <span className="text-muted-foreground">
                              {v.projectName}
                            </span>
                          )}
                        </td>
                        {canManage && (
                          <td className="py-3">
                            <div className="flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7"
                                onClick={() => openEditEnv(v)}
                              >
                                <Pencil className="h-3.5 w-3.5" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-destructive"
                                onClick={() => handleDeleteEnv(v.id)}
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Edit Env Var Dialog */}
        <Dialog open={editEnvDialogOpen} onOpenChange={setEditEnvDialogOpen}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Edit Variable</DialogTitle>
              <DialogDescription>
                Update the value for{" "}
                <code className="font-mono text-foreground">{editEnvName}</code>.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleEditEnv} className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Scope</label>
                <Select
                  value={editEnvScope}
                  onChange={(e) => setEditEnvScope(e.target.value)}
                  options={scopeOptions}
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Name</label>
                <Input value={editEnvName} readOnly className="bg-muted" />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Value</label>
                <Input
                  placeholder="value"
                  value={editEnvValue}
                  onChange={(e) => setEditEnvValue(e.target.value)}
                  autoFocus
                />
              </div>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setEditEnvDialogOpen(false)}
                >
                  Cancel
                </Button>
                <Button type="submit" disabled={savingEnv}>
                  {savingEnv ? "Saving…" : "Save Changes"}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </AppLayout>
  );
}
