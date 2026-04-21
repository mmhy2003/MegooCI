"use client";

import * as React from "react";
import { toast } from "sonner";
import { formatDistanceToNow } from "date-fns";
import { KeyRound, Plus, Trash2, Variable } from "lucide-react";
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
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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

interface ProjectSecrets {
  project: Project;
  secrets: Secret[];
  envVars: EnvVar[];
}

export default function SecretsPage() {
  const confirm = useConfirm();
  const canManage = usePermission("secrets.manage");
  const [projectData, setProjectData] = React.useState<ProjectSecrets[]>([]);
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [loading, setLoading] = React.useState(true);

  const [secretDialogOpen, setSecretDialogOpen] = React.useState(false);
  const [secProjectId, setSecProjectId] = React.useState("");
  const [secName, setSecName] = React.useState("");
  const [secValue, setSecValue] = React.useState("");
  const [creatingSec, setCreatingSec] = React.useState(false);

  const [envDialogOpen, setEnvDialogOpen] = React.useState(false);
  const [envProjectId, setEnvProjectId] = React.useState("");
  const [envName, setEnvName] = React.useState("");
  const [envValue, setEnvValue] = React.useState("");
  const [creatingEnv, setCreatingEnv] = React.useState(false);

  async function loadData() {
    try {
      const allProjects = await projectsApi.list();
      setProjects(allProjects);

      const data = await Promise.all(
        allProjects.map(async (project) => {
          const [secs, vars] = await Promise.all([
            secretsApi.list("project", project.id).catch(() => []),
            envVarsApi.list("project", project.id).catch(() => []),
          ]);
          return {
            project,
            secrets: secs as Secret[],
            envVars: vars as EnvVar[],
          };
        }),
      );
      setProjectData(data);
      if (allProjects.length > 0) {
        setSecProjectId(allProjects[0].id);
        setEnvProjectId(allProjects[0].id);
      }
    } catch {
      toast.error("Failed to load secrets data");
    } finally {
      setLoading(false);
    }
  }

  React.useEffect(() => {
    loadData();
  }, []);

  const allSecrets = projectData.flatMap((pd) =>
    pd.secrets.map((s) => ({ ...s, projectName: pd.project.name })),
  );
  const allEnvVars = projectData.flatMap((pd) =>
    pd.envVars.map((v) => ({ ...v, projectName: pd.project.name })),
  );

  async function handleAddSecret(e: React.FormEvent) {
    e.preventDefault();
    if (!secName.trim() || !secValue.trim() || !secProjectId) {
      toast.error("All fields are required");
      return;
    }
    setCreatingSec(true);
    try {
      await secretsApi.create({
        scope_type: "project",
        scope_id: secProjectId,
        name: secName,
        value: secValue,
      });
      setSecretDialogOpen(false);
      setSecName("");
      setSecValue("");
      toast.success("Secret added");
      loadData();
    } catch {
      toast.error("Failed to add secret");
    } finally {
      setCreatingSec(false);
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
          will be permanently removed from{" "}
          <span className="font-medium text-foreground">
            {secret?.projectName ?? "the project"}
          </span>
          . Pipelines using it will fail until it&apos;s recreated.
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

  async function handleAddEnv(e: React.FormEvent) {
    e.preventDefault();
    if (!envName.trim() || !envValue.trim() || !envProjectId) {
      toast.error("All fields are required");
      return;
    }
    setCreatingEnv(true);
    try {
      await envVarsApi.create({
        scope_type: "project",
        scope_id: envProjectId,
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

  async function handleDeleteEnv(envId: string) {
    const envVar = allEnvVars.find((v) => v.id === envId);
    const ok = await confirm({
      title: "Delete this variable?",
      description: (
        <>
          <code className="font-mono text-foreground">
            {envVar?.name ?? "This variable"}
          </code>{" "}
          will be removed from{" "}
          <span className="font-medium text-foreground">
            {envVar?.projectName ?? "the project"}
          </span>
          .
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

  const projectOptions = projects.map((p) => ({
    value: p.id,
    label: p.name,
  }));

  return (
    <AppLayout>
      <div className="space-y-8">
        <div>
          <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
            Secrets & Variables
          </h1>
          <p className="text-sm text-muted-foreground sm:text-base">
            Manage secrets and environment variables across your projects.
          </p>
        </div>

        {/* Secrets Section */}
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
                  disabled={projects.length === 0}
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
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleAddSecret} className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Project</label>
                    <Select
                      value={secProjectId}
                      onChange={(e) => setSecProjectId(e.target.value)}
                      options={projectOptions}
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
                    <Input
                      type="password"
                      placeholder="••••••••"
                      value={secValue}
                      onChange={(e) => setSecValue(e.target.value)}
                    />
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
                      <th className="hidden pb-3 pr-4 font-medium md:table-cell">
                        Type
                      </th>
                      <th className="pb-3 pr-4 font-medium">Project</th>
                      <th className="hidden pb-3 pr-4 font-medium sm:table-cell">
                        Created
                      </th>
                      <th className="pb-3 font-medium w-12"></th>
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
                        <td className="hidden py-3 pr-4 text-muted-foreground md:table-cell">
                          {secret.secret_type}
                        </td>
                        <td className="py-3 pr-4 text-muted-foreground">
                          {secret.projectName}
                        </td>
                        <td className="hidden py-3 pr-4 text-muted-foreground sm:table-cell">
                          {formatDistanceToNow(new Date(secret.created_at), {
                            addSuffix: true,
                          })}
                        </td>
                        {canManage && (
                          <td className="py-3">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 text-destructive"
                              onClick={() => handleDeleteSecret(secret.id)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
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

        {/* Environment Variables Section */}
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
                  disabled={projects.length === 0}
                >
                  <Plus className="mr-1.5 h-4 w-4" />
                  Add Variable
                </Button>
              )}
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Add Environment Variable</DialogTitle>
                </DialogHeader>
                <form onSubmit={handleAddEnv} className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Project</label>
                    <Select
                      value={envProjectId}
                      onChange={(e) => setEnvProjectId(e.target.value)}
                      options={projectOptions}
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
                      <th className="pb-3 pr-4 font-medium">Project</th>
                      <th className="pb-3 font-medium w-12"></th>
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
                        <td className="py-3 pr-4 text-muted-foreground">
                          {v.projectName}
                        </td>
                        {canManage && (
                          <td className="py-3">
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 text-destructive"
                              onClick={() => handleDeleteEnv(v.id)}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
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
      </div>
    </AppLayout>
  );
}
