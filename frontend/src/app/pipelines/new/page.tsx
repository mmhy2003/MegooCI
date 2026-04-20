"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArrowLeft } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { pipelinesApi, projectsApi, type Project } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select } from "@/components/ui/select";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

const YAML_STARTER = `version: 1
name: my-pipeline

stages:
  - name: build
    steps:
      - run: echo "Installing dependencies..."
      - run: echo "Building project..."

  - name: test
    steps:
      - run: echo "Running tests..."

  - name: deploy
    when:
      branch: main
    steps:
      - run: echo "Deploying..."
`;

export default function NewPipelinePage() {
  const router = useRouter();
  const [projects, setProjects] = React.useState<Project[]>([]);
  const [name, setName] = React.useState("");
  const [projectId, setProjectId] = React.useState("");
  const [sourceRepo, setSourceRepo] = React.useState("");
  const [defaultBranch, setDefaultBranch] = React.useState("main");
  const [definitionFormat, setDefinitionFormat] = React.useState<
    "yaml" | "python"
  >("yaml");
  const [yamlContent, setYamlContent] = React.useState(YAML_STARTER);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  React.useEffect(() => {
    projectsApi
      .list()
      .then((data) => {
        setProjects(data);
        if (data.length > 0) setProjectId(data[0].id);
      })
      .catch(() => toast.error("Failed to load projects"));
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) {
      toast.error("Pipeline name is required");
      return;
    }
    if (!projectId) {
      toast.error("Please select a project");
      return;
    }
    setIsSubmitting(true);
    try {
      const pipeline = await pipelinesApi.create({
        project_id: projectId,
        name,
        source_repo_url: sourceRepo || undefined,
        default_branch: defaultBranch,
        definition_format: definitionFormat,
        yaml_content: yamlContent,
      });
      toast.success("Pipeline created!");
      router.push(`/pipelines/${pipeline.id}`);
    } catch {
      toast.error("Failed to create pipeline");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <AppLayout>
      <div className="mx-auto max-w-3xl space-y-6">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => router.push("/pipelines")}
        >
          <ArrowLeft className="mr-1.5 h-4 w-4" />
          Back to Pipelines
        </Button>

        <Card>
          <CardHeader>
            <CardTitle>Create Pipeline</CardTitle>
            <CardDescription>
              Define a new CI/CD pipeline with YAML or Python.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleSubmit} className="space-y-6">
              <div className="space-y-2">
                <label htmlFor="name" className="text-sm font-medium">
                  Pipeline name
                </label>
                <Input
                  id="name"
                  placeholder="e.g. frontend-build"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoFocus
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="project" className="text-sm font-medium">
                  Project
                </label>
                <Select
                  id="project"
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                  options={projects.map((p) => ({
                    value: p.id,
                    label: p.name,
                  }))}
                  placeholder="Select a project"
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="repo" className="text-sm font-medium">
                  Source repository URL{" "}
                  <span className="text-muted-foreground">(optional)</span>
                </label>
                <Input
                  id="repo"
                  placeholder="https://github.com/org/repo"
                  value={sourceRepo}
                  onChange={(e) => setSourceRepo(e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <label htmlFor="branch" className="text-sm font-medium">
                  Default branch
                </label>
                <Input
                  id="branch"
                  placeholder="main"
                  value={defaultBranch}
                  onChange={(e) => setDefaultBranch(e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <span className="text-sm font-medium">Definition format</span>
                <div className="flex gap-4">
                  <label className="flex cursor-pointer items-center gap-2">
                    <input
                      type="radio"
                      name="format"
                      value="yaml"
                      checked={definitionFormat === "yaml"}
                      onChange={() => setDefinitionFormat("yaml")}
                      className="accent-primary"
                    />
                    <span className="text-sm">YAML</span>
                  </label>
                  <label className="flex cursor-pointer items-center gap-2">
                    <input
                      type="radio"
                      name="format"
                      value="python"
                      checked={definitionFormat === "python"}
                      onChange={() => setDefinitionFormat("python")}
                      className="accent-primary"
                    />
                    <span className="text-sm">Python</span>
                  </label>
                </div>
              </div>

              <div className="space-y-2">
                <label htmlFor="config" className="text-sm font-medium">
                  Pipeline definition
                </label>
                <Textarea
                  id="config"
                  className="min-h-[320px] font-mono text-sm"
                  value={yamlContent}
                  onChange={(e) => setYamlContent(e.target.value)}
                  spellCheck={false}
                />
              </div>

              <div className="flex flex-col-reverse gap-2 sm:flex-row sm:gap-3">
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => router.push("/pipelines")}
                  className="w-full sm:w-auto"
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={isSubmitting}
                  className="w-full sm:w-auto"
                >
                  {isSubmitting ? "Creating…" : "Create Pipeline"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </AppLayout>
  );
}
