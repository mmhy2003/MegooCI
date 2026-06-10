"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import {
  Bell,
  Boxes,
  ChevronDown,
  ChevronRight,
  Terminal,
  Container,
  GitBranch,
  MonitorSmartphone,
  Webhook,
  UserCheck,
  Lock,
  BookOpen,
  Copy,
  Check,
  Play,
  Package,
  Server,
  FileEdit,
  CopyPlus,
  Trash2,
  Bot,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";

interface DocSection {
  id: string;
  title: string;
  icon: React.ReactNode;
  description: string;
  yaml: string;
}

const DOCS: DocSection[] = [
  {
    id: "structure",
    title: "Pipeline Structure",
    icon: <BookOpen className="h-4 w-4" />,
    description:
      "Every pipeline starts with a version, name, optional global env vars, an optional runs_on targeting the build agent, and a list of stages. Each stage has steps.",
    yaml: `version: 1
name: my-pipeline
runs_on: linux          # optional — route the whole build to a Linux agent
env:
  APP_NAME: myapp

stages:
  - name: build
    steps:
      - run: "echo Hello"

  - name: deploy
    when:
      branch: main
    steps:
      - run: "echo Deploying..."`,
  },
  {
    id: "runs_on",
    title: "Target Agent (runs_on)",
    icon: <Server className="h-4 w-4" />,
    description:
      "Route the entire build to a specific agent environment. `runs_on` is declared once at the top of the YAML — every stage in the build runs on the same agent. Use the shorthand `runs_on: <os>` to pin to an OS (linux, windows, darwin), or the full form with arch and labels for finer control. Agents are registered under Settings → Agents with their OS, architecture, and labels. If no matching agent is online, the build stays pending until one connects (or an admin re-enables a disabled agent). Omit `runs_on` entirely to accept any online agent.",
    yaml: `# Shorthand — pin the build to a Linux agent
version: 1
name: build-app
runs_on: linux
stages:
  - name: build
    steps:
      - run: "go build ./..."

# Full form — match os + arch + labels
version: 1
name: package-windows
runs_on:
  os: windows
  arch: amd64
  labels: [docker]        # agent must carry ALL listed labels
stages:
  - name: package
    steps:
      - run: "msbuild app.sln"

# No runs_on — any online agent will do
version: 1
name: notify-only
stages:
  - name: notify
    steps:
      - notify:
          channel: "deploy-alerts"
          message: "Build done"`,
  },
  {
    id: "run",
    title: "Shell Commands (run)",
    icon: <Terminal className="h-4 w-4" />,
    description:
      "Execute any shell command. Supports multi-command chains with && and environment variables.",
    yaml: `- name: install-and-test
  run: "npm install && npm test"
  env:
    NODE_ENV: production`,
  },
  {
    id: "write_file",
    title: "Write File",
    icon: <FileEdit className="h-4 w-4" />,
    description:
      "Create a file with the specified content at a given path. Parent directories are created automatically. Works cross-platform (Linux, macOS, Windows). Great for generating config files, Dockerfiles, or scripts on the fly.",
    yaml: `# Create a config file
- name: write-config
  write_file:
    path: ./config.json
    content: |
      { "env": "production", "debug": false }

# Generate a Dockerfile
- name: generate-dockerfile
  write_file:
    path: ./Dockerfile
    content: |
      FROM node:22-alpine
      WORKDIR /app
      COPY . .
      RUN npm ci --production
      CMD ["node", "server.js"]`,
  },
  {
    id: "copy_files",
    title: "Copy Files",
    icon: <CopyPlus className="h-4 w-4" />,
    description:
      "Copy files or entire directories from a source to a destination. Parent directories at the destination are created automatically. When copying a directory, all contents are copied recursively. Works cross-platform.",
    yaml: `# Copy a single file
- name: copy-binary
  copy_files:
    source: ./build/output/app.exe
    destination: ./dist/app.exe

# Copy an entire directory (recursive)
- name: copy-assets
  copy_files:
    source: ./build/output
    destination: ./dist`,
  },
  {
    id: "delete_files",
    title: "Delete Files",
    icon: <Trash2 className="h-4 w-4" />,
    description:
      "Delete files or directories. Use 'path' for a single target or 'paths' for multiple. Directories are removed recursively. No error if the path doesn't exist.",
    yaml: `# Delete a single path
- name: clean-temp
  delete_files:
    path: ./temp

# Delete multiple paths
- name: clean-all
  delete_files:
    paths:
      - ./temp
      - ./cache
      - ./build/output`,
  },
  {
    id: "ai_agent",
    title: "AI Agent",
    icon: <Bot className="h-4 w-4" />,
    description:
      "Run the Pi AI coding agent with a prompt. The agent reads the codebase, reasons about the task, and modifies files autonomously. Requires an API key for the LLM provider. Default provider is Anthropic, default timeout is 300 seconds.",
    yaml: `# Basic usage (Anthropic, default model)
- name: ai-refactor
  ai_agent:
    prompt: "Refactor error handling in src/api/ to use a centralized error handler"
    api_key: \${{ secrets.ANTHROPIC_API_KEY }}

# Full options (OpenAI, specific model, custom timeout)
- name: ai-add-tests
  ai_agent:
    prompt: "Add unit tests for the User model"
    api_key: \${{ secrets.OPENAI_API_KEY }}
    provider: openai
    model: gpt-4o
    timeout: 600`,
  },
  {
    id: "docker_login",
    title: "Docker Login",
    icon: <Container className="h-4 w-4" />,
    description:
      "Authenticate with a container registry before pushing/pulling images. You can use user credentials via secrets, or use a registry deploy token (created under Container Registry → Deploy Tokens). Deploy tokens use the fixed username 'deploy-token'.",
    yaml: `# Option 1: User credentials via secrets
- docker_login:
    registry: ghcr.io
    username: \${{ secrets.GHCR_USER }}
    password: \${{ secrets.GHCR_TOKEN }}

# Option 2: MegooCI built-in registry deploy token
# Create a global deploy token under Registry → Deploy Tokens.
# Store the token value as a secret (e.g. REGISTRY_TOKEN).
- docker_login:
    registry: megooci-registry.example.com
    username: deploy-token
    password: \${{ secrets.REGISTRY_TOKEN }}`,
  },
  {
    id: "docker_build",
    title: "Docker Build",
    icon: <Container className="h-4 w-4" />,
    description:
      "Build a Docker image from a Dockerfile. Supports tags, build args, multi-stage targets, and platform.",
    yaml: `- docker_build:
    context: "."
    dockerfile: Dockerfile
    tags:
      - "ghcr.io/org/app:latest"
      - "ghcr.io/org/app:\${{ env.VERSION }}"
    build_args:
      NODE_ENV: production
    target: runtime        # optional
    no_cache: false        # optional
    platform: linux/amd64  # optional`,
  },
  {
    id: "docker_push",
    title: "Docker Push",
    icon: <Container className="h-4 w-4" />,
    description: "Push one or more image tags to a registry. For the MegooCI built-in registry, images can use a single-segment name (registry/project-slug:tag) or two segments (registry/project-slug/repo-name:tag). Single-segment names are stored under a default repository.",
    yaml: `# Push to any registry
- docker_push:
    tags:
      - "ghcr.io/org/app:latest"
      - "ghcr.io/org/app:v1.2.3"

# Push to MegooCI built-in registry (single-segment)
- docker_push:
    tags:
      - "megooci-registry.example.com/my-project:latest"

# Push to MegooCI built-in registry (two-segment)
- docker_push:
    tags:
      - "megooci-registry.example.com/my-project/api:latest"`,
  },
  {
    id: "git_clone",
    title: "Git Clone",
    icon: <GitBranch className="h-4 w-4" />,
    description:
      "Clone a repository. Supports branch selection, shallow clones, and private repo authentication via token. If you have a connected Git provider, the token is auto-injected.",
    yaml: `# Public repo
- git_clone:
    repo: "https://github.com/org/repo.git"
    branch: main
    depth: 1
    path: "."

# Private repo — use a secret token
- git_clone:
    repo: "https://github.com/org/private-repo.git"
    token: \${{ secrets.GIT_TOKEN }}
    branch: main`,
  },
  {
    id: "git_pull_push",
    title: "Git Pull / Push",
    icon: <GitBranch className="h-4 w-4" />,
    description: "Pull the latest changes or push commits to a remote.",
    yaml: `# Pull
- git_pull:
    remote: origin
    branch: main

# Push
- git_push:
    remote: origin
    branch: main
    force: false`,
  },
  {
    id: "ssh_exec",
    title: "SSH Remote Execute",
    icon: <MonitorSmartphone className="h-4 w-4" />,
    description:
      "Connect to a remote server via SSH and run commands. Supports key-based auth (recommended) and password-based auth via sshpass. Always use secrets for credentials.",
    yaml: `# Key-based auth (recommended)
- ssh_exec:
    host: deploy.example.com
    port: 22
    user: deploy
    private_key: \${{ secrets.SSH_KEY }}
    commands:
      - "cd /opt/app && docker compose pull"
      - "docker compose up -d"
    env:
      APP_VERSION: "1.2.3"

# Password-based auth
- ssh_exec:
    host: deploy.example.com
    user: deploy
    password: \${{ secrets.SSH_PASSWORD }}
    commands:
      - "systemctl restart myapp"`,
  },
  {
    id: "kube_apply",
    title: "Kubernetes Apply",
    icon: <Boxes className="h-4 w-4" />,
    description:
      "Apply Kubernetes manifests and wait for the rollout to become ready. Store the kubeconfig as a secret — never inline it. The build fails if any apply fails or a workload doesn't become ready within the timeout.",
    yaml: `- kube_apply:
    kubeconfig: \${{ secrets.PROD_KUBECONFIG }}
    manifests:
      - k8s/deployment.yaml
      - k8s/service.yaml
    namespace: production   # optional
    context: prod-cluster   # optional kubeconfig context
    timeout: 300            # optional rollout wait in seconds (default 300)`,
  },
  {
    id: "wait_webhook",
    title: "Wait for Webhook",
    icon: <Webhook className="h-4 w-4" />,
    description:
      "Pause the pipeline until an external system sends a webhook callback. Useful for waiting on deployments, tests, or third-party approvals.",
    yaml: `- wait_webhook:
    name: "health-check"
    timeout: 3600
    match:
      event: deployment_complete`,
  },
  {
    id: "wait_input",
    title: "Wait for User Approval",
    icon: <UserCheck className="h-4 w-4" />,
    description:
      "Pause the pipeline until a user manually approves or rejects. Great for production deployment gates.",
    yaml: `- wait_input:
    prompt: "Deploy to production?"
    timeout: 86400
    allowed_users:
      - admin
      - lead`,
  },
  {
    id: "notify",
    title: "Send Notification",
    icon: <Bell className="h-4 w-4" />,
    description:
      "Send a notification through a configured channel (email, Slack, or Telegram). Channels are set up by admins under Integrations > Notification Channels.",
    yaml: `- name: notify-team
  notify:
    channel: "deploy-alerts"
    message: |
      Build finished on branch \${{ build.branch }}
      Commit: \${{ build.commit_sha }}
    subject: "Build Report"       # optional (email only)
    recipient: "#deployments"     # optional override`,
  },
  {
    id: "trigger_pipeline",
    title: "Trigger Pipeline",
    icon: <Play className="h-4 w-4" />,
    description:
      "Trigger another pipeline from within a running pipeline. Use to chain CI and deploy pipelines, fan-out builds, or orchestrate multi-repo workflows. Optionally wait for the triggered build to finish.",
    yaml: `- name: trigger-deploy
  trigger_pipeline:
    pipeline: "deploy-production"   # name or UUID
    branch: main                    # optional
    params:                         # optional
      VERSION: "1.2.3"
    wait: true                      # block until child finishes
    timeout: 3600                   # max wait seconds (default: 3600)`,
  },
  {
    id: "artifacts",
    title: "Artifacts",
    icon: <Package className="h-4 w-4" />,
    description:
      "Collect build outputs (binaries, reports, bundles) after a stage's steps succeed. Declare an artifacts.paths list on the stage with glob patterns resolved from the workspace root. Files are stored on the server and downloadable from the build detail page and the /artifacts page; retention is governed by system settings.",
    yaml: `stages:
  - name: build
    steps:
      - run: "go build -o dist/myapp ./cmd/app"
      - run: "go test -coverprofile=coverage/report.out ./..."
    artifacts:
      paths:
        - "dist/*"
        - "coverage/report.out"
        - "build/reports/**/*.html"`,
  },
  {
    id: "secrets",
    title: "Secrets & Variables",
    icon: <Lock className="h-4 w-4" />,
    description:
      "Reference secrets and environment variables anywhere in your pipeline using placeholder syntax. Secrets are decrypted at runtime and masked in logs.",
    yaml: `# In any string value:
\${{ secrets.API_KEY }}
\${{ secrets.SSH_DEPLOY_KEY }}
\${{ env.NODE_ENV }}
\${{ env.GIT_SHA }}

# Example usage in a step:
- run: "curl -H 'Authorization: Bearer \${{ secrets.API_KEY }}' https://api.example.com"`,
  },
];

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = React.useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }, [text]);

  return (
    <button
      type="button"
      onClick={handleCopy}
      className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
    >
      {copied ? (
        <>
          <Check className="h-3 w-3" />
          Copied
        </>
      ) : (
        <>
          <Copy className="h-3 w-3" />
          Copy
        </>
      )}
    </button>
  );
}

function InsertButton({
  text,
  onInsert,
}: {
  text: string;
  onInsert?: (yaml: string) => void;
}) {
  if (!onInsert) return null;
  return (
    <button
      type="button"
      onClick={() => onInsert(text)}
      className="flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-primary hover:bg-primary/10 transition-colors"
    >
      Insert
    </button>
  );
}

interface DocsPanelProps {
  className?: string;
  onInsert?: (yaml: string) => void;
  /** Called when the user clicks the close button in the header. */
  onClose?: () => void;
}

export function DocsPanel({ className, onInsert, onClose }: DocsPanelProps) {
  const [expanded, setExpanded] = React.useState<string | null>("structure");

  return (
    <div className={cn("flex h-full flex-col", className)}>
      <div className="flex items-center justify-between border-b px-5 py-3.5">
        <div className="flex items-center gap-2.5">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/10">
            <BookOpen className="h-4 w-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-none">Pipeline Reference</h3>
            <p className="mt-0.5 text-[11px] text-muted-foreground">Step documentation</p>
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
      <ScrollArea className="min-h-0 flex-1">
        <div className="divide-y">
          {DOCS.map((doc) => {
            const isOpen = expanded === doc.id;
            return (
              <div key={doc.id}>
                <button
                  type="button"
                  onClick={() => setExpanded(isOpen ? null : doc.id)}
                  className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm hover:bg-muted/50 transition-colors"
                >
                  {isOpen ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <span className="shrink-0 text-primary">{doc.icon}</span>
                  <span className="font-medium">{doc.title}</span>
                </button>
                {isOpen && (
                  <div className="px-4 pb-3">
                    <p className="mb-2 text-xs text-muted-foreground leading-relaxed">
                      {doc.description}
                    </p>
                    <div className="relative rounded-md bg-muted/50 border">
                      <div className="flex items-center justify-between border-b px-3 py-1">
                        <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                          YAML
                        </span>
                        <div className="flex gap-1">
                          <CopyButton text={doc.yaml} />
                          <InsertButton text={doc.yaml} onInsert={onInsert} />
                        </div>
                      </div>
                      <pre className="overflow-x-auto p-3 text-xs leading-relaxed">
                        <code>{doc.yaml}</code>
                      </pre>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </ScrollArea>
    </div>
  );
}
