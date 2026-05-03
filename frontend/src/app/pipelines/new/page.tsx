"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { ArrowLeft } from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import {
  pipelinesApi,
  projectsApi,
  projectRepositoriesApi,
  type Project,
  type ProjectRepository,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { PipelineEditor } from "@/components/pipeline/pipeline-editor";
import { AiAssistantPanel } from "@/components/pipeline/ai-assistant-panel";
import { DocsPanel } from "@/components/pipeline/docs-panel";
import { VarsPanel } from "@/components/pipeline/vars-panel";
import { Sheet } from "@/components/ui/sheet";
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
  const [repositories, setRepositories] = React.useState<ProjectRepository[]>([]);
  const [projectRepositoryId, setProjectRepositoryId] = React.useState<string>("");
  const [name, setName] = React.useState("");
  const [projectId, setProjectId] = React.useState("");
  const [sourceRepo, setSourceRepo] = React.useState("");
  const [defaultBranch, setDefaultBranch] = React.useState("main");
  const [yamlContent, setYamlContent] = React.useState(YAML_STARTER);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [aiOpen, setAiOpen] = React.useState(false);
  const [docsOpen, setDocsOpen] = React.useState(false);
  const [varsOpen, setVarsOpen] = React.useState(false);

  React.useEffect(() => {
    projectsApi
      .list()
      .then((data) => {
        setProjects(data);
        if (data.length > 0) setProjectId(data[0].id);
      })
      .catch(() => toast.error("Failed to load projects"));
  }, []);

  // Whenever the chosen project changes, load its linked repositories so the
  // user can pick one instead of typing a URL by hand (PRD §6.16 / F-16.5).
  React.useEffect(() => {
    if (!projectId) {
      setRepositories([]);
      setProjectRepositoryId("");
      return;
    }
    let cancelled = false;
    projectRepositoriesApi
      .list(projectId)
      .then((data) => {
        if (cancelled) return;
        setRepositories(data);
        // Reset link when switching projects.
        setProjectRepositoryId("");
      })
      .catch(() => {
        if (!cancelled) setRepositories([]);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId]);

  // When a linked repo is picked, auto-fill the repo URL + default branch so
  // the user sees what will be used. They can still override both fields.
  function onLinkedRepoChange(repoId: string) {
    setProjectRepositoryId(repoId);
    if (!repoId) return;
    const repo = repositories.find((r) => r.id === repoId);
    if (repo) {
      setSourceRepo(repo.repo_url);
      setDefaultBranch(repo.default_branch);
    }
  }

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
        project_repository_id: projectRepositoryId || null,
        source_repo_url: sourceRepo || undefined,
        default_branch: defaultBranch,
        definition_format: "yaml",
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

  function handleApplyYaml(yaml: string) {
    setYamlContent(yaml);
  }

  return (
    <AppLayout>
      <div className="mx-auto max-w-5xl space-y-6">
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
              Define a new CI/CD pipeline with YAML.
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

              {repositories.length > 0 && (
                <div className="space-y-2">
                  <label htmlFor="linked-repo" className="text-sm font-medium">
                    Linked repository{" "}
                    <span className="text-muted-foreground">(optional)</span>
                  </label>
                  <Select
                    id="linked-repo"
                    value={projectRepositoryId}
                    onChange={(e) => onLinkedRepoChange(e.target.value)}
                    options={[
                      { value: "", label: "\u2014 None (use URL below) \u2014" },
                      ...repositories.map((r) => ({
                        value: r.id,
                        label: r.display_name
                          ? `${r.display_name} (${r.repo_url})`
                          : r.repo_url,
                      })),
                    ]}
                  />
                  <p className="text-xs text-muted-foreground">
                    Picking a linked repository wires webhook-driven builds
                    and fills the URL / branch fields below.
                  </p>
                </div>
              )}

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
                  readOnly={Boolean(projectRepositoryId)}
                  className={projectRepositoryId ? "bg-muted" : ""}
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

              <PipelineEditor
                value={yamlContent}
                onChange={setYamlContent}
                minHeight="320px"
                placeholder="Enter your YAML pipeline definition..."
                aiOpen={aiOpen}
                onToggleAi={() => setAiOpen((prev) => !prev)}
                docsOpen={docsOpen}
                onToggleDocs={() => setDocsOpen((prev) => !prev)}
                varsOpen={varsOpen}
                onToggleVars={() => setVarsOpen((prev) => !prev)}
              />

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

      {/* AI Assistant Drawer */}
      <Sheet open={aiOpen} onOpenChange={setAiOpen}>
        <AiAssistantPanel
          currentYaml={yamlContent}
          onApplyYaml={handleApplyYaml}
          projectId={projectId || null}
          onClose={() => setAiOpen(false)}
        />
      </Sheet>

      {/* Docs Drawer */}
      <Sheet open={docsOpen} onOpenChange={setDocsOpen}>
        <DocsPanel
          onInsert={(yaml) => {
            const trimmed = yamlContent.trimEnd();
            setYamlContent(trimmed ? `${trimmed}\n\n${yaml}\n` : `${yaml}\n`);
          }}
          onClose={() => setDocsOpen(false)}
        />
      </Sheet>

      {/* Vars Drawer */}
      <Sheet open={varsOpen} onOpenChange={setVarsOpen}>
        <VarsPanel
          projectId={projectId || null}
          onInsert={(snippet) => {
            const trimmed = yamlContent.trimEnd();
            setYamlContent(trimmed ? `${trimmed} ${snippet}` : snippet);
          }}
          onClose={() => setVarsOpen(false)}
        />
      </Sheet>
    </AppLayout>
  );
}
