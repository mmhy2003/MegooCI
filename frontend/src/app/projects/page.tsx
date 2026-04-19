"use client";

import * as React from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import { toast } from "sonner";
import { Plus, FolderKanban } from "lucide-react";
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

export default function ProjectsPage() {
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const [creating, setCreating] = React.useState(false);

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

  return (
    <AppLayout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Projects</h1>
            <p className="text-muted-foreground">
              {projects.length} project{projects.length !== 1 ? "s" : ""}
            </p>
          </div>
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <Button onClick={() => setDialogOpen(true)}>
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
            {projects.map((project) => (
              <Link key={project.id} href={`/projects/${project.id}`}>
                <Card className="h-full transition-shadow hover:shadow-lg cursor-pointer">
                  <CardHeader>
                    <CardTitle className="text-base">{project.name}</CardTitle>
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
            ))}
          </div>
        )}
      </div>
    </AppLayout>
  );
}
