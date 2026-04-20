"use client";

import * as React from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { Plus, FolderKanban, Trash2, Loader2 } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { projectsApi, type Project } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { useConfirm } from "@/components/ui/confirm-dialog";

export default function ProjectsPage() {
  const confirm = useConfirm();
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [creating, setCreating] = React.useState(false);
  const [deletingId, setDeletingId] = React.useState<string | null>(null);

  const [newName, setNewName] = React.useState("");
  const [newDesc, setNewDesc] = React.useState("");

  React.useEffect(() => {
    projectsApi
      .list()
      .then(setProjects)
      .catch(() => toast.error("Failed to load projects"))
      .finally(() => setLoading(false));
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) {
      toast.error("Project name is required");
      return;
    }
    setCreating(true);
    try {
      const project = await projectsApi.create({
        name: newName,
        description: newDesc || undefined,
      });
      setProjects((prev) => [project, ...prev]);
      setDialogOpen(false);
      setNewName("");
      setNewDesc("");
      toast.success("Project created!");
    } catch {
      toast.error("Failed to create project");
    } finally {
      setCreating(false);
    }
  }

  async function handleDeleteProject(project: Project) {
    // Mirrors the detail-page flow: try a plain delete first, and if the
    // backend refuses because dependents exist (409 with a `cannot delete
    // project` detail), offer a second confirmation that retries with
    // ?force=true to cascade.
    const ok = await confirm({
      title: `Delete project '${project.name}'?`,
      description: (
        <>
          The project will be removed. Pipelines, linked repositories, secrets,
          and environment variables scoped to this project must already be
          empty. This action cannot be undone.
        </>
      ),
      confirmText: "Delete project",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;

    setDeletingId(project.id);
    try {
      await projectsApi.delete(project.id);
      setProjects((prev) => prev.filter((p) => p.id !== project.id));
      toast.success("Project deleted");
      return;
    } catch (err: unknown) {
      const body = (err as { body?: { detail?: string } } | undefined)?.body;
      const detail = body?.detail;

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
        await projectsApi.delete(project.id, { force: true });
        setProjects((prev) => prev.filter((p) => p.id !== project.id));
        toast.success("Project and its contents deleted");
      } catch (err2: unknown) {
        const body2 = (err2 as { body?: { detail?: string } } | undefined)
          ?.body;
        toast.error(
          body2?.detail ||
            (err2 instanceof Error ? err2.message : "Failed to delete project"),
        );
      }
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight sm:text-2xl">
              Projects
            </h1>
            <p className="text-sm text-muted-foreground sm:text-base">
              {projects.length} project{projects.length !== 1 ? "s" : ""}
            </p>
          </div>
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <Button
              onClick={() => setDialogOpen(true)}
              className="w-full sm:w-auto"
            >
              <Plus className="mr-1.5 h-4 w-4" />
              New Project
            </Button>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Create Project</DialogTitle>
                <DialogDescription>
                  Add a new project to organize your pipelines.
                </DialogDescription>
              </DialogHeader>
              <form onSubmit={handleCreate} className="space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Name</label>
                  <Input
                    placeholder="my-project"
                    value={newName}
                    onChange={(e) => setNewName(e.target.value)}
                    autoFocus
                  />
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Description</label>
                  <Textarea
                    placeholder="What is this project for?"
                    value={newDesc}
                    onChange={(e) => setNewDesc(e.target.value)}
                    rows={3}
                  />
                </div>
                <DialogFooter>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" disabled={creating}>
                    {creating ? "Creating…" : "Create Project"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        {loading ? (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={i}>
                <CardHeader>
                  <Skeleton className="h-5 w-32" />
                  <Skeleton className="h-4 w-48" />
                </CardHeader>
                <CardContent>
                  <Skeleton className="h-4 w-24" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : projects.length === 0 ? (
          <Card>
            <CardContent className="flex flex-col items-center justify-center py-16">
              <div className="mb-4 rounded-full bg-muted p-4">
                <FolderKanban className="h-8 w-8 text-muted-foreground" />
              </div>
              <h3 className="mb-1 text-lg font-semibold">No projects yet</h3>
              <p className="mb-6 max-w-sm text-center text-sm text-muted-foreground">
                Create your first project to start organizing your pipelines.
              </p>
              <Button onClick={() => setDialogOpen(true)}>
                <Plus className="mr-1.5 h-4 w-4" />
                Create Project
              </Button>
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((project) => {
              const isDeleting = deletingId === project.id;
              return (
                <div key={project.id} className="group relative">
                  <Link href={`/projects/${project.id}`} className="block">
                    <Card className="h-full transition-shadow hover:shadow-lg cursor-pointer">
                      <CardHeader>
                        <CardTitle className="pr-9 text-base">
                          {project.name}
                        </CardTitle>
                        {project.description && (
                          <CardDescription className="line-clamp-2">
                            {project.description}
                          </CardDescription>
                        )}
                      </CardHeader>
                      <CardContent className="text-sm text-muted-foreground">
                        <p className="font-mono text-xs">{project.slug}</p>
                        <p className="mt-1 text-xs">
                          Created{" "}
                          {formatDistanceToNow(new Date(project.created_at), {
                            addSuffix: true,
                          })}
                        </p>
                      </CardContent>
                    </Card>
                  </Link>
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Delete project ${project.name}`}
                    disabled={isDeleting}
                    onClick={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      handleDeleteProject(project);
                    }}
                    className="absolute right-2 top-2 h-8 w-8 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 group-hover:opacity-100"
                  >
                    {isDeleting ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Trash2 className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </AppLayout>
  );
}
