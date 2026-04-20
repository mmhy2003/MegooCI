"use client";

import * as React from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import {
  ArrowLeft,
  GitBranch,
  Plus,
  KeyRound,
  Trash2,
  Settings,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { ProjectIntegrations } from "@/components/project-integrations";
import {
  projectsApi,
  pipelinesApi,
  secretsApi,
  envVarsApi,
  type Project,
  type Pipeline,
  type Secret,
  type EnvVar,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

type Tab = "pipelines" | "integrations" | "settings";

export default function ProjectDetailPage() {
  const params = useParams();
  const router = useRouter();
  const confirm = useConfirm();
  const id = params.id as string;

  const [project, setProject] = React.useState<Project | null>(null);
  const [pipelines, setPipelines] = React.useState<Pipeline[]>([]);
  const [secrets, setSecrets] = React.useState<Secret[]>([]);
  const [envVars, setEnvVars] = React.useState<EnvVar[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [activeTab, setActiveTab] = React.useState<Tab>("pipelines");

  const [secretDialogOpen, setSecretDialogOpen] = React.useState(false);
  const [newSecretName, setNewSecretName] = React.useState("");
  const [newSecretValue, setNewSecretValue] = React.useState("");
  const [creatingSec, setCreatingSec] = React.useState(false);

  const [envDialogOpen, setEnvDialogOpen] = React.useState(false);
  const [newEnvName, setNewEnvName] = React.useState("");
  const [newEnvValue, setNewEnvValue] = React.useState("");
  const [creatingEnv, setCreatingEnv] = React.useState(false);

  React.useEffect(() => {
    async function load() {
      try {
        const [p, pipes, secs, vars] = await Promise.all([
          projectsApi.get(id),
          pipelinesApi.list({ project_id: id }),
          secretsApi.list("project", id).catch(() => [] as Secret[]),
          envVarsApi.list("project", id).catch(() => [] as EnvVar[]),
        ]);
        setProject(p);
        setPipelines(pipes);
        setSecrets(secs);
        setEnvVars(vars);
      } catch {
        toast.error("Failed to load project");
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [id]);

  async function handleDeleteProject() {
    // First attempt: plain delete. The backend returns 409 with a human-
    // readable detail listing exactly what still references the project.
    // We catch that, surface the detail, and offer a cascade ("Delete
    // everything") option that retries with ?force=true.
    const ok = await confirm({
      title: `Delete project '${project?.name ?? ""}'?`,
      description: (
        <>
          The project will be removed. Pipelines, linked repositories,
          secrets, and environment variables scoped to this project must
          already be empty. This action cannot be undone.
        </>
      ),
      confirmText: "Delete project",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;

    try {
      await projectsApi.delete(id);
      toast.success("Project deleted");
      router.push("/projects");
      return;
    } catch (err: unknown) {
      // Walk our ApiError body for the FastAPI `detail` string.
      const body = (err as { body?: { detail?: string } } | undefined)?.body;
      const detail = body?.detail;

      // If the delete was refused because dependents still exist, offer the
      // user a force-cascade path. Anything else (403, 404, 500) we just
      // surface as a toast.
      const isDependentsConflict =
        typeof detail === "string" &&
        detail.toLowerCase().includes("cannot delete project");

      if (!isDependentsConflict) {
        toast.error(
          detail ||
            (err instanceof Error ? err.message : "Failed to delete project"),
        );
        return;
      }

      const forceOk = await confirm({
        title: "Delete everything in this project?",
        description: (
          <>
            <p>{detail}</p>
            <p className="mt-2 text-sm">
              Proceeding will permanently remove{" "}
              <span className="font-medium text-foreground">
                all pipelines, linked repositories, webhook history, secrets,
                and environment variables
              </span>{" "}
              that belong to this project, then delete the project itself.
            </p>
          </>
        ),
        confirmText: "Delete everything",
        cancelText: "Cancel",
        tone: "destructive",
      });
      if (!forceOk) return;

      try {
        await projectsApi.delete(id, { force: true });
        toast.success("Project and its contents deleted");
        router.push("/projects");
      } catch (err2: unknown) {
        const body2 = (err2 as { body?: { detail?: string } } | undefined)
          ?.body;
        toast.error(
          body2?.detail ||
            (err2 instanceof Error ? err2.message : "Failed to delete project"),
        );
      }
    }
  }

  async function handleAddSecret(e: React.FormEvent) {
    e.preventDefault();
    if (!newSecretName.trim() || !newSecretValue.trim()) {
      toast.error("Name and value are required");
      return;
    }
    setCreatingSec(true);
    try {
      const secret = await secretsApi.create({
        scope_type: "project",
        scope_id: id,
        name: newSecretName,
        value: newSecretValue,
      });
      setSecrets((prev) => [...prev, secret]);
      setSecretDialogOpen(false);
      setNewSecretName("");
      setNewSecretValue("");
      toast.success("Secret added");
    } catch {
      toast.error("Failed to add secret");
    } finally {
      setCreatingSec(false);
    }
  }

  async function handleDeleteSecret(secretId: string) {
    const secret = secrets.find((s) => s.id === secretId);
    const ok = await confirm({
      title: "Delete this secret?",
      description: (
        <>
          <code className="font-mono text-foreground">
            {secret?.name ?? "This secret"}
          </code>{" "}
          will be permanently removed. Pipelines using it will fail until it&apos;s
          recreated.
        </>
      ),
      confirmText: "Delete secret",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await secretsApi.delete(secretId);
      setSecrets((prev) => prev.filter((s) => s.id !== secretId));
      toast.success("Secret deleted");
    } catch {
      toast.error("Failed to delete secret");
    }
  }

  async function handleAddEnv(e: React.FormEvent) {
    e.preventDefault();
    if (!newEnvName.trim() || !newEnvValue.trim()) {
      toast.error("Name and value are required");
      return;
    }
    setCreatingEnv(true);
    try {
      const envVar = await envVarsApi.create({
        scope_type: "project",
        scope_id: id,
        name: newEnvName,
        value: newEnvValue,
      });
      setEnvVars((prev) => [...prev, envVar]);
      setEnvDialogOpen(false);
      setNewEnvName("");
      setNewEnvValue("");
      toast.success("Environment variable added");
    } catch {
      toast.error("Failed to add environment variable");
    } finally {
      setCreatingEnv(false);
    }
  }

  async function handleDeleteEnv(envId: string) {
    const envVar = envVars.find((v) => v.id === envId);
    const ok = await confirm({
      title: "Delete this variable?",
      description: (
        <>
          <code className="font-mono text-foreground">
            {envVar?.name ?? "This variable"}
          </code>{" "}
          will be removed from this project.
        </>
      ),
      confirmText: "Delete variable",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;
    try {
      await envVarsApi.delete(envId);
      setEnvVars((prev) => prev.filter((v) => v.id !== envId));
      toast.success("Variable deleted");
    } catch {
      toast.error("Failed to delete variable");
    }
  }

  const tabs: { key: Tab; label: string }[] = [
    { key: "pipelines", label: "Pipelines" },
    { key: "integrations", label: "Integrations" },
    { key: "settings", label: "Settings" },
  ];

  if (loading) {
    return (
      <AppLayout>
        <div className="space-y-6">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-48" />
          <div className="grid gap-4 sm:grid-cols-2">
            <Skeleton className="h-32" />
            <Skeleton className="h-32" />
          </div>
        </div>
      </AppLayout>
    );
  }

  if (!project) {
    return (
      <AppLayout>
        <div className="py-16 text-center">
          <p className="text-muted-foreground">Project not found.</p>
          <Button
            variant="link"
            className="mt-2"
            onClick={() => router.push("/projects")}
          >
            Back to Projects
          </Button>
        </div>
      </AppLayout>
    );
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push("/projects")}
        >
          <ArrowLeft className="mr-1.5 h-4 w-4" />
          Projects
        </Button>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <h1 className="break-all text-xl font-bold sm:text-2xl">
              {project.name}
            </h1>
            {project.description && (
              <p className="mt-1 text-sm text-muted-foreground sm:text-base">
                {project.description}
              </p>
            )}
            <p className="mt-1 break-all font-mono text-xs text-muted-foreground sm:text-sm">
              {project.slug}
            </p>
          </div>
          <Button
            variant="outline"
            size="sm"
            className="text-destructive hover:text-destructive"
            onClick={handleDeleteProject}
          >
            <Trash2 className="mr-1.5 h-4 w-4" />
            Delete project
          </Button>
        </div>

        <div className="-mx-4 flex gap-1 overflow-x-auto border-b px-4 sm:mx-0 sm:px-0">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`shrink-0 border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === tab.key
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "pipelines" && (
          <div className="space-y-4">
            {pipelines.length === 0 ? (
              <Card>
                <CardContent className="flex flex-col items-center justify-center py-12">
                  <GitBranch className="mb-3 h-8 w-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">
                    No pipelines in this project yet.
                  </p>
                  <Button
                    className="mt-4"
                    size="sm"
                    onClick={() => router.push("/pipelines/new")}
                  >
                    <Plus className="mr-1.5 h-4 w-4" />
                    Create Pipeline
                  </Button>
                </CardContent>
              </Card>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2">
                {pipelines.map((pipe) => (
                  <Link key={pipe.id} href={`/pipelines/${pipe.id}`}>
                    <Card className="h-full transition-shadow hover:shadow-lg cursor-pointer">
                      <CardHeader>
                        <div className="flex items-center justify-between">
                          <CardTitle className="text-base">
                            {pipe.name}
                          </CardTitle>
                          <Badge variant="secondary">
                            {pipe.definition_format.toUpperCase()}
                          </Badge>
                        </div>
                      </CardHeader>
                      <CardContent className="text-xs text-muted-foreground">
                        Created{" "}
                        {formatDistanceToNow(new Date(pipe.created_at), {
                          addSuffix: true,
                        })}
                      </CardContent>
                    </Card>
                  </Link>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === "integrations" && <ProjectIntegrations projectId={id} />}

        {activeTab === "settings" && (
          <div className="space-y-6">
            <Card>
              <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
                <CardTitle className="text-base flex items-center gap-2">
                  <KeyRound className="h-4 w-4" />
                  Secrets
                </CardTitle>
                <Dialog
                  open={secretDialogOpen}
                  onOpenChange={setSecretDialogOpen}
                >
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setSecretDialogOpen(true)}
                  >
                    <Plus className="mr-1.5 h-4 w-4" />
                    Add Secret
                  </Button>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Add Secret</DialogTitle>
                      <DialogDescription>
                        Secrets are encrypted and never displayed after creation.
                      </DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleAddSecret} className="space-y-4">
                      <div className="space-y-2">
                        <label className="text-sm font-medium">Name</label>
                        <Input
                          placeholder="SECRET_NAME"
                          value={newSecretName}
                          onChange={(e) => setNewSecretName(e.target.value)}
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium">Value</label>
                        <Input
                          type="password"
                          placeholder="••••••••"
                          value={newSecretValue}
                          onChange={(e) => setNewSecretValue(e.target.value)}
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
                {secrets.length === 0 ? (
                  <p className="py-4 text-center text-sm text-muted-foreground">
                    No secrets configured
                  </p>
                ) : (
                  <div className="space-y-2">
                    {secrets.map((secret) => (
                      <div
                        key={secret.id}
                        className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2.5 text-sm sm:px-4"
                      >
                        <code className="min-w-0 flex-1 truncate font-medium">
                          {secret.name}
                        </code>
                        <div className="flex shrink-0 items-center gap-2 text-muted-foreground sm:gap-3">
                          <span className="hidden text-xs sm:inline">
                            {formatDistanceToNow(new Date(secret.created_at), {
                              addSuffix: true,
                            })}
                          </span>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-7 w-7 text-destructive"
                            onClick={() => handleDeleteSecret(secret.id)}
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="flex flex-row items-center justify-between gap-2 space-y-0">
                <CardTitle className="text-base flex items-center gap-2">
                  <Settings className="h-4 w-4" />
                  Environment Variables
                </CardTitle>
                <Dialog open={envDialogOpen} onOpenChange={setEnvDialogOpen}>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setEnvDialogOpen(true)}
                  >
                    <Plus className="mr-1.5 h-4 w-4" />
                    Add Variable
                  </Button>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Add Environment Variable</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleAddEnv} className="space-y-4">
                      <div className="space-y-2">
                        <label className="text-sm font-medium">Name</label>
                        <Input
                          placeholder="ENV_NAME"
                          value={newEnvName}
                          onChange={(e) => setNewEnvName(e.target.value)}
                        />
                      </div>
                      <div className="space-y-2">
                        <label className="text-sm font-medium">Value</label>
                        <Input
                          placeholder="value"
                          value={newEnvValue}
                          onChange={(e) => setNewEnvValue(e.target.value)}
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
                {envVars.length === 0 ? (
                  <p className="py-4 text-center text-sm text-muted-foreground">
                    No environment variables configured
                  </p>
                ) : (
                  <div className="space-y-2">
                    {envVars.map((v) => (
                      <div
                        key={v.id}
                        className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2.5 text-sm sm:px-4"
                      >
                        <div className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-3">
                          <code className="truncate font-medium">
                            {v.name}
                          </code>
                          <span className="truncate text-xs text-muted-foreground sm:text-sm">
                            {v.is_secret_ref ? "••••••" : v.value}
                          </span>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7 shrink-0 text-destructive"
                          onClick={() => handleDeleteEnv(v.id)}
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </AppLayout>
  );
}
