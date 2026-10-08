"""
AI pipeline assistant — generates or modifies megooci.yaml content based on
natural language prompts.

Uses LiteLLM for unified multi-provider LLM support. LiteLLM automatically
handles provider-specific parameter translation (reasoning models, Anthropic
native API, Azure, Ollama, etc.) via a single ``completion()`` interface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("uvicorn.error")

import litellm
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import database
from app.api.v1.agents import _normalize_status
from app.config import get_settings
from app.core.access import has_global_permission, project_id_for_pipeline
from app.core.deps import (
    check_scoped_permission,
    effective_scoped_permissions,
    get_current_active_user,
)
from app.database import get_db
from app.models.agent import Agent
from app.models.git_integration import ProjectRepository
from app.models.pipeline import Pipeline
from app.models.secret import EnvVar, Secret
from app.models.user import User
from app.services.assistant.diff import build_proposal
from app.services.assistant.document import MAX_DOCUMENT_BYTES, DocumentError, WorkingDocument
from app.services.assistant.loop import LoopResult, ModelTurn, OnStep, Step, ToolCall, run_loop
from app.services.assistant.reference import build_tool_prompt, split_topics
from app.services.assistant.tools import TOOL_DEFINITIONS, ToolContext
from app.services.pipeline_compiler import validate_pipeline_definition

# Let LiteLLM silently drop unsupported params per model (e.g. temperature
# for reasoning models) instead of raising errors.
litellm.drop_params = True

router = APIRouter()

# Map MegooCI provider names to LiteLLM model prefixes.
_PROVIDER_PREFIX: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "ollama": "ollama",
    "azure_openai": "azure",
    "custom": "openai",  # OpenAI-compatible endpoints
}


def _build_model_id(ai_cfg: dict[str, object]) -> str:
    """Map MegooCI provider + model to LiteLLM's ``provider/model`` format."""
    provider = str(ai_cfg.get("provider") or "openai")
    model = str(ai_cfg.get("model") or "gpt-4o-mini")
    prefix = _PROVIDER_PREFIX.get(provider, "openai")
    return f"{prefix}/{model}"

SYSTEM_PROMPT = """\
You are MegooCI Pipeline Assistant — an expert at writing CI/CD pipeline \
definitions in YAML for the MegooCI platform.

## Available Step Types

### run — Execute shell commands
```yaml
- run: "npm install && npm run build"
```

### write_file — Create a file with specified content
```yaml
# Simple file
- write_file:
    path: ./config.json
    content: |
      { "env": "production", "debug": false }

# Generate a Dockerfile
- write_file:
    path: ./Dockerfile
    content: |
      FROM node:22-alpine
      WORKDIR /app
      COPY . .
      RUN npm ci --production
      CMD ["node", "server.js"]
```

`path` is relative to the workspace root. Parent directories are created \
automatically. Works cross-platform (Linux, macOS, Windows).

### copy_files — Copy files or directories
```yaml
# Copy a single file
- copy_files:
    source: ./build/output/app.exe
    destination: ./dist/app.exe

# Copy an entire directory (recursive)
- copy_files:
    source: ./build/output
    destination: ./dist
```

`source` and `destination` are relative to the workspace root. Parent \
directories for the destination are created automatically. When copying a \
directory, all contents are copied recursively.

### delete_files — Delete files or directories
```yaml
# Delete a single path
- delete_files:
    path: ./temp

# Delete multiple paths
- delete_files:
    paths:
      - ./temp
      - ./cache
      - ./build/output
```

Use `path` for a single target or `paths` for multiple. Directories are \
removed recursively. No error if the path doesn't exist.

### ai_agent — Run an AI coding agent with a prompt
```yaml
# Basic usage (Anthropic, default model)
- ai_agent:
    prompt: "Refactor error handling in src/api/ to use a centralized error handler"
    api_key: ${{ secrets.ANTHROPIC_API_KEY }}

# Full options
- ai_agent:
    prompt: "Add unit tests for the User model in tests/models/"
    api_key: ${{ secrets.OPENAI_API_KEY }}
    provider: openai
    model: gpt-4o
    timeout: 600
```

The `ai_agent` step runs the Pi coding agent (`pi -p`) in the workspace. \
The agent reads the codebase, reasons about the prompt, and makes changes \
to files autonomously. `api_key` is injected as the provider's expected \
environment variable (e.g. `ANTHROPIC_API_KEY` for Anthropic, \
`OPENAI_API_KEY` for OpenAI). Always use `${{ secrets.X }}` for the key. \
Default provider is `anthropic`, default timeout is 300 seconds.

### docker_login — Authenticate with a container registry
```yaml
# Option 1: User credentials via secrets
- docker_login:
    registry: ghcr.io
    username: ${{ secrets.GHCR_USER }}
    password: ${{ secrets.GHCR_TOKEN }}

# Option 2: MegooCI built-in registry deploy token
# Create a global deploy token under Registry → Deploy Tokens.
# Store the token value as a secret (e.g. REGISTRY_TOKEN).
- docker_login:
    registry: megooci-registry.example.com
    username: deploy-token
    password: ${{ secrets.REGISTRY_TOKEN }}
```

Authentication options:
1. **User credentials** — standard username/password stored as secrets.
2. **Deploy tokens** — created under Container Registry → Deploy Tokens. \
Deploy tokens can be **global** (access all projects) or scoped to a single \
project. The username is always the literal string `deploy-token` and the \
password is the token value. Store the token in a secret for safety.

### docker_build — Build a Docker image
```yaml
- docker_build:
    context: "."
    dockerfile: Dockerfile
    tags:
      - "ghcr.io/org/app:latest"
      - "ghcr.io/org/app:${{ env.VERSION }}"
    build_args:
      NODE_ENV: production
    target: runtime        # optional multi-stage target
    no_cache: false        # optional
    platform: linux/amd64  # optional
```

### docker_push — Push image(s) to a registry
```yaml
- docker_push:
    tags:
      - "ghcr.io/org/app:latest"
```

For the **MegooCI built-in registry**, image names can be:
- **Single-segment**: `registry-host/project-slug:tag` — stored under a default \
repository automatically.
- **Two-segment**: `registry-host/project-slug/repo-name:tag` — stored under \
the named repository.

Example built-in registry push:
```yaml
- docker_push:
    tags:
      - "megooci-registry.example.com/my-project:latest"
```

### git_clone — Clone a repository
```yaml
# Public repo
- git_clone:
    repo: "https://github.com/org/repo.git"
    branch: main
    depth: 1     # optional shallow clone
    path: "."    # optional checkout path

# Private repo — explicit token from a secret
- git_clone:
    repo: "https://github.com/org/private-repo.git"
    token: ${{ secrets.GIT_TOKEN }}
    branch: main
```

Private repo authentication (resolved in priority order):
1. Explicit `token` field — use `${{ secrets.GIT_TOKEN }}` to inject from secrets.
2. A secret named `GIT_TOKEN` in the pipeline/project scope.
3. Auto-inject — if the project has a connected Git provider (Settings → Integrations) \
whose hostname matches the repo URL, MegooCI injects the stored token automatically.

### git_pull — Pull latest changes
```yaml
- git_pull:
    remote: origin
    branch: main
```

### git_push — Push commits
```yaml
- git_push:
    remote: origin
    branch: main
    force: false
```

### ssh_exec — Execute commands on a remote server via SSH
```yaml
# Key-based auth (recommended)
- ssh_exec:
    host: deploy.example.com
    port: 22
    user: deploy
    private_key: ${{ secrets.SSH_KEY }}
    commands:
      - "cd /opt/app && docker compose pull"
      - "docker compose up -d"
    env:
      APP_VERSION: "1.2.3"

# Password-based auth (uses sshpass)
- ssh_exec:
    host: deploy.example.com
    user: deploy
    password: ${{ secrets.SSH_PASSWORD }}
    commands:
      - "systemctl restart myapp"
```

Authentication is resolved in order: `private_key` → `password` → ssh-agent. \
For password auth, `sshpass` must be installed in the agent environment (included \
by default in the official MegooCI agent image). Always use `${{ secrets.X }}` for \
credentials — never hardcode passwords or keys in the YAML.

### kube_apply — Apply Kubernetes manifests and wait for rollout
```yaml
- kube_apply:
    kubeconfig: ${{ secrets.PROD_KUBECONFIG }}
    manifests:
      - k8s/deployment.yaml
      - k8s/service.yaml
    namespace: production    # optional
    context: prod-cluster    # optional kubeconfig context
    timeout: 300             # optional rollout wait in seconds (default 300)
```

The kubeconfig must come from a secret — never inline it. After applying, the \
step waits for every applied Deployment/StatefulSet/DaemonSet to finish \
rolling out and fails the build if any doesn't become ready within the \
timeout. Directory entries in `manifests` are applied non-recursively. All \
manifests in a step should target the step's `namespace` — the rollout wait \
looks there, so a manifest declaring a different `metadata.namespace` would \
fail the wait.

### http_request — Send an HTTP request to an external system
```yaml
- http_request:
    url: ${{ secrets.DEPLOY_WEBHOOK_URL }}   # required; http:// or https://
    method: POST                             # optional: GET, POST, PUT, PATCH, DELETE (default POST)
    headers:                                 # optional
      Authorization: Bearer ${{ secrets.DEPLOY_API_TOKEN }}
    json:                                    # optional; any mapping or list, sent as JSON
      text: "Build #${{ build.number }} of ${{ pipeline.name }} deployed"
      branch: ${{ build.branch }}
      tags: [ci, production]
    timeout: 30                              # optional seconds per attempt (default 30, max 300)
    retries: 2                               # optional extra attempts (default 0, max 5)
    expect_status: [200, 202]                # optional; one code or a list (default: any 2xx)
    verify_tls: true                         # optional; false accepts a self-signed certificate
```

For a payload that is not JSON, use `body` instead of `json` and set the content \
type yourself:
```yaml
- http_request:
    url: https://legacy.example.com/hook
    headers:
      Content-Type: application/xml
    body: |
      <deploy><build>${{ build.number }}</build></deploy>
```

Use `http_request` to call any external URL: a chat webhook (Teams, Discord), a \
deployment or ticketing system, or the user's own service. The payload has no \
fixed shape — write whatever the receiving system expects under `json`. \
`json` and `body` cannot be combined, and both are optional. Only these fields \
exist: `url`, `method`, `headers`, `json`, `body`, `timeout`, `retries`, \
`expect_status`, `verify_tls`; any other field is rejected.

Placeholders always produce strings, so `count: ${{ build.number }}` sends \
`"42"`, while literal YAML numbers and booleans keep their type. When a number \
or boolean must come from a placeholder, use `body` with hand-written JSON and a \
`Content-Type: application/json` header.

The step fails the build when the response status is not the expected one. \
Retries apply to network errors, 5xx and 429 only. Redirects are not followed, \
and the response cannot be used by later steps. The request is sent from the \
build agent. The first 2000 characters of the response are written to the build log, \
so do not use this step to call an endpoint that returns a secret. \
Most webhook URLs contain a token, so take the URL from \
`${{ secrets.X }}`, and take every credential from a secret too. To send through \
a channel configured in Notification Channels, use `notify` instead.

### wait_webhook — Pause until an external webhook callback
```yaml
- wait_webhook:
    name: "deployment-ready"
    timeout: 3600
    match:
      event: deployment_complete
```

### wait_input — Pause until a user approves/rejects
```yaml
- wait_input:
    prompt: "Deploy to production?"
    timeout: 86400
    allowed_users:
      - admin
      - lead
```

### notify — Send a notification via a configured channel
```yaml
- notify:
    channel: "deploy-alerts"   # channel name from Admin > Notification Channels
    message: |
      Build finished with status on branch ${{ build.branch }}
      Commit: ${{ build.commit }}
    subject: "Build Report"    # optional (used by email channels)
    recipient: "#deployments"  # optional channel/chat_id/email override
```

Supported channel types: email (SMTP), Slack (webhook), Telegram (bot).
Channels are configured by admins in the Notification Channels UI.
A `notify` step runs only if the build reaches it. A build stops at the first \
failed step, so a `notify` step never reports a failure — use the top-level \
`notifications` block for that.

### trigger_pipeline — Trigger another pipeline
```yaml
- trigger_pipeline:
    pipeline: "deploy-production"   # pipeline name or UUID
    branch: main                    # optional (defaults to target's default_branch)
    params:                         # optional parameters forwarded to the child build
      VERSION: "1.2.3"
    wait: true                      # optional — block until triggered build finishes (default: false)
    timeout: 3600                   # optional — max seconds to wait (default: 3600, only when wait=true)
```

Use `trigger_pipeline` to chain pipelines — e.g. a CI pipeline triggers a deploy \
pipeline after tests pass. Set `wait: true` when the parent pipeline should fail if \
the child pipeline fails. Reference the target by its pipeline name or UUID.

## Artifacts — Collect build outputs
Stages can declare an `artifacts` key to collect files after all steps succeed.
Glob patterns are resolved relative to the workspace root.

```yaml
stages:
  - name: build
    steps:
      - run: "go build -o dist/myapp ./cmd/app"
    artifacts:
      paths:
        - "dist/*"
        - "coverage/report.html"
```

Artifacts are stored on the server and available for download from the build
detail page. Retention is governed by the system artifact retention settings.

## Targeting agents (runs_on)
A pipeline can declare which build environment it needs with `runs_on` at \
the **top level** of the YAML (not inside a stage). The dispatcher then \
picks an agent whose registered `os`, `arch`, and `labels` match — the \
whole build runs on that single agent. Agents are registered by admins \
under Settings → Agents.

```yaml
# Shorthand — pin the build to a Linux agent
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
  labels: [docker]      # agent must carry ALL listed labels
stages:
  - name: package
    steps:
      - run: "msbuild app.sln"
```

Allowed `os` values: `linux`, `windows`, `darwin`.
Allowed `arch` values: `amd64`, `arm64` (aliases `x86_64`, `aarch64` accepted).
`labels` is a list of strings — the agent must carry every label listed.

Important rules:
- `runs_on` is **pipeline-level only**. Putting it inside a stage is a \
validation error — move it to the top of the YAML.
- A build runs on a single agent end-to-end. There is no per-stage agent \
switching.
- Omit `runs_on` to accept any online agent — appropriate for OS-agnostic \
work like `notify`-only pipelines.
- If no matching agent is online, the build stays pending until one connects \
or an operator re-enables a disabled agent.

## Notifications — Tell people when a build fails
Add a top-level `notifications` block (not inside a stage) to send a message \
through a configured channel whenever a build of this pipeline fails. The \
server sends it, so it also goes out when the build's agent goes offline or \
stops responding. A build that is still waiting for an agent stays pending and sends nothing.

```yaml
version: 1
name: deploy-staging
notifications:
  on_failure:
    - deploy-alerts                     # short form: a channel name
    - channel: ops-email                # full form
      recipient: oncall@example.com     # optional; overrides the channel's default
      subject: "Staging deploy failed"  # optional; used by email channels
      message: |                        # optional; a default is sent if omitted
        Build #${{ build.number }} of ${{ pipeline.name }} failed
        at ${{ build.failed_stage }} / ${{ build.failed_step }}
        ${{ build.url }}
stages:
  - name: deploy
    steps:
      - run: "./deploy.sh"
```

`on_failure` is the only event, and it must be a non-empty list. Each entry is \
either a channel name or a mapping whose only fields are `channel` (required), \
`message`, `subject` and `recipient`. Channels are configured by admins in the \
Notification Channels UI (email, Slack or Telegram) — never invent a channel \
name; ask the user which channel to use if they have not said.

Without `message`, a default is sent: the pipeline name, build number, branch, \
commit, the stage and step that failed, and a link to the build. A custom \
`message` or `subject` can use every placeholder, plus three that exist only \
here: `${{ build.failed_stage }}`, `${{ build.failed_step }}` and \
`${{ build.url }}`. For an email channel set `recipient`; without it the \
message goes to the channel's own sender address. A successful or cancelled \
build sends nothing.

## Pipeline Structure
```yaml
version: 1
name: pipeline-name
runs_on: linux          # optional — target a specific agent environment
notifications:          # optional — who is told when a build fails
  on_failure:
    - channel-name
env:                    # global env vars (inherited by all stages/steps)
  KEY: value

stages:
  - name: stage-name
    when:               # conditional execution (optional)
      branch: main
    env:                # stage-level env (merges with global)
      KEY: value
    steps:
      - name: step-name # optional display name
        run: "command"
        env:            # step-level env (merges with stage)
          KEY: value
    artifacts:          # optional — collect files after stage completes
      paths:
        - "dist/*"
```

## Placeholders
- `${{ secrets.NAME }}` — replaced at runtime with decrypted project/pipeline secrets
- `${{ env.NAME }}` — replaced at runtime with environment variables
- `${{ build.number }}`, `${{ build.branch }}`, `${{ build.commit }}`, `${{ pipeline.name }}`, `${{ project.name }}` — details of the running build

## Rules
1. Always output valid YAML.
2. When the user asks to **generate** a new pipeline, output the complete YAML \
inside a ```yaml fenced code block. You may add a brief explanation AFTER the \
YAML block, but keep it short.
3. When the user asks to **modify, fix, or update** an existing pipeline, you MUST \
output the **complete, updated pipeline YAML** inside a ```yaml fenced code block — \
not just the changed fragment or a single stage. The user's editor will replace the \
entire pipeline with your output, so partial snippets will break their pipeline. \
You may add a brief summary of what you changed AFTER the YAML block.
4. NEVER output only the changed portion of a pipeline. Always return the full \
pipeline from `version:` through every stage, even if only one line changed.
5. When the user asks a question about syntax (without requesting a change), \
answer concisely and include a short YAML example.
6. Always use `${{ secrets.X }}` for sensitive values — never hardcode passwords.
7. For `notify` steps, always use a configured channel name. For `http_request` \
steps, take the URL from `${{ secrets.X }}` whenever it contains a token — never hardcode \
webhook URLs or bot tokens in the YAML.
8. Use realistic, production-quality examples.
9. When a pipeline builds binaries, compiles code, or generates reports, include \
an `artifacts.paths` section on the relevant stage to collect the outputs.
10. Only add `runs_on` when the user mentions a specific OS / architecture / agent, \
or when the work is obviously OS-specific (e.g. `msbuild`, `apt-get`, PowerShell-only \
commands). Otherwise omit `runs_on` so any online agent can pick up the build. \
Place `runs_on` at the top of the YAML (pipeline-level) — never inside a stage. \
Never invent OS or arch values — stick to the allowed set above.
11. When modifying or fixing a pipeline, use **inline YAML comments** (`# ...`) \
to explain what you changed and why, directly next to the affected lines. This \
makes the pipeline self-documenting. Keep your chat reply brief — a one-line \
summary is enough since the YAML comments carry the detail.
12. When the user wants to be told about failed builds, add the top-level \
`notifications` block with `on_failure` — never a `notify` step at the end of \
the pipeline, which does not run after a failure.
"""

# With tools, the prompt is re-sent on every model call, so the step and
# feature sections are served by the `reference` tool instead of being in it.
REFERENCE_TOPICS = split_topics(SYSTEM_PROMPT)
TOOL_SYSTEM_PROMPT = build_tool_prompt(SYSTEM_PROMPT, REFERENCE_TOPICS)


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class AssistantRequest(BaseModel):
    prompt: str
    current_yaml: str | None = None
    project_id: str | None = None
    pipeline_id: str | None = None
    repo_url: str | None = None
    branch: str | None = None
    history: list[ChatMessage] | None = None


class AssistantStep(BaseModel):
    """One tool call the assistant made, as shown in the chat."""

    tool: str
    label: str
    ok: bool = True


class AssistantProblem(BaseModel):
    message: str
    line: int | None = None


class AssistantDiffLine(BaseModel):
    kind: str  # "context" | "add" | "remove"
    old: int | None = None
    new: int | None = None
    text: str


class AssistantDiffHunk(BaseModel):
    old_start: int
    new_start: int
    lines: list[AssistantDiffLine]


class AssistantProposal(BaseModel):
    """The YAML the assistant proposes, and how it differs from the editor's."""

    yaml: str
    added: int
    removed: int
    problems: list[AssistantProblem] = []
    hunks: list[AssistantDiffHunk] = []


class AssistantResponse(BaseModel):
    reply: str
    # Same as proposal.yaml; kept for clients that predate the proposal.
    yaml: str | None = None
    mode: str = "tools"  # "tools" | "legacy"
    limit_reached: bool = False
    steps: list[AssistantStep] = []
    proposal: AssistantProposal | None = None


async def _build_project_context(
    db: AsyncSession, project_id: str, *, include_values: bool = False,
) -> str | None:
    """Fetch secret names and env var names for a project and return a
    context block the LLM can reference.

    Plaintext env var values are only included when *include_values* is
    True (i.e. the caller has ``secrets.read`` permission).
    """
    try:
        pid = uuid.UUID(project_id)
    except ValueError:
        return None

    secrets_q = (
        select(Secret.name)
        .where(Secret.scope_type == "project", Secret.scope_id == pid)
        .order_by(Secret.name)
    )
    env_vars_q = (
        select(EnvVar.name, EnvVar.value, EnvVar.is_secret_ref)
        .where(EnvVar.scope_type == "project", EnvVar.scope_id == pid)
        .order_by(EnvVar.name)
    )

    secrets_result = await db.execute(secrets_q)
    env_vars_result = await db.execute(env_vars_q)

    secret_names = [row[0] for row in secrets_result.all()]
    env_vars = env_vars_result.all()

    if not secret_names and not env_vars:
        return None

    parts: list[str] = []

    if secret_names:
        names_list = ", ".join(f"`{n}`" for n in secret_names)
        parts.append(
            f"Available project secrets (use via ${{{{ secrets.NAME }}}}): {names_list}"
        )

    if env_vars:
        var_lines = []
        for name, value, is_ref in env_vars:
            if is_ref:
                var_lines.append(f"  - `{name}` (references a secret)")
            elif include_values:
                var_lines.append(f"  - `{name}` = `{value}`")
            else:
                var_lines.append(f"  - `{name}`")
        parts.append(
            "Available project environment variables (use via ${{ env.NAME }}):\n"
            + "\n".join(var_lines)
        )

    return "\n\n".join(parts)


async def _build_repo_context(
    db: AsyncSession,
    *,
    repo_url: str | None = None,
    branch: str | None = None,
    pipeline_id: str | None = None,
) -> str | None:
    """Build a system-prompt section describing the current repository and branch.

    Priority:
    1. Explicit ``repo_url`` / ``branch`` passed from the frontend form fields.
    2. If a ``pipeline_id`` is provided, look up the Pipeline row. If it links
       to a ``ProjectRepository`` (via ``project_repository_id``), use the repo
       data from there — including ``display_name``. Otherwise fall back to the
       Pipeline's own ``source_repo_url`` / ``default_branch``.
    """

    display_name: str | None = None

    # ── Try to resolve from the Pipeline row when explicit values are missing ──
    if pipeline_id and (not repo_url):
        try:
            pid = uuid.UUID(pipeline_id)
        except ValueError:
            pid = None

        if pid is not None:
            pipeline = await db.get(Pipeline, pid)
            if pipeline is not None:
                # Prefer the linked ProjectRepository for richer data.
                if pipeline.project_repository_id:
                    proj_repo = await db.get(ProjectRepository, pipeline.project_repository_id)
                    if proj_repo is not None:
                        repo_url = repo_url or proj_repo.repo_url
                        branch = branch or proj_repo.default_branch
                        display_name = proj_repo.display_name

                # Fall back to the pipeline's own fields.
                repo_url = repo_url or pipeline.source_repo_url
                branch = branch or pipeline.default_branch

    if not repo_url and not branch:
        return None

    parts: list[str] = ["\n\n## Repository Context"]

    if display_name:
        parts.append(f"Repository display name: **{display_name}**")
    if repo_url:
        parts.append(
            f"Repository URL: `{repo_url}` — when generating `git_clone` steps, "
            "use this URL instead of a placeholder."
        )
    if branch:
        parts.append(
            f"Default branch: `{branch}` — use this as the branch in `git_clone`, "
            "`when.branch`, and similar fields unless the user specifies otherwise."
        )

    return "\n".join(parts)


MAX_AGENTS_LISTED = 50


@dataclass
class _Job:
    """Everything one assistant request needs, gathered while the request's
    database session is still open."""

    model_id: str
    ai_cfg: dict
    tool_messages: list[Any]
    legacy_messages: list[Any]
    current_yaml: str | None
    # None when the user may not see agents.
    list_agents: Callable[[], Awaitable[str]] | None


def _history(body: AssistantRequest) -> list[dict[str, str]]:
    return [
        {"role": msg.role, "content": msg.content}
        for msg in body.history or []
        if msg.role in ("user", "assistant")
    ]


def _legacy_messages(body: AssistantRequest, system_content: str) -> list[Any]:
    """The conversation for a model called without tools: it gets the whole
    YAML and must return the whole YAML."""
    messages: list[Any] = [{"role": "system", "content": system_content}, *_history(body)]

    if body.current_yaml:
        messages.append({
            "role": "user",
            "content": (
                "Here is my current pipeline YAML from the editor "
                "(this reflects the latest state, including any manual edits I made).\n"
                "IMPORTANT: When I ask you to modify, fix, or update this pipeline, "
                "you MUST return the COMPLETE updated pipeline YAML inside a "
                "```yaml code block — not just the changed part. My editor replaces "
                "the entire pipeline with your output.\n\n"
                f"```yaml\n{body.current_yaml}\n```"
            ),
        })
        messages.append({
            "role": "assistant",
            "content": (
                "Got it — I can see your full pipeline YAML. "
                "When you ask me to make changes, I'll always return the "
                "complete updated pipeline in a ```yaml block so you can "
                "apply it directly. What would you like me to do?"
            ),
        })

    messages.append({"role": "user", "content": body.prompt})
    return messages


def _tool_messages(body: AssistantRequest, system_content: str) -> list[Any]:
    """The conversation for a model called with tools. The YAML is shown with
    line numbers so the model can edit short pipelines without reading first."""
    document = WorkingDocument(body.current_yaml)
    if document.line_count:
        editor = (
            f"The pipeline YAML in my editor right now ({document.line_count} lines, "
            f"each prefixed with its line number):\n{document.read_lines()}"
        )
    else:
        editor = "My editor is empty: there is no pipeline YAML yet."
    return [
        {"role": "system", "content": system_content},
        *_history(body),
        {"role": "user", "content": f"{editor}\n\nMy request: {body.prompt}"},
    ]


def _agent_lister() -> Callable[[], Awaitable[str]]:
    """The ``list_agents`` tool. It opens its own session because the
    request's session may be closed by the time a streamed reply calls it."""

    async def list_agents() -> str:
        async with database.async_session() as session:
            result = await session.execute(
                select(Agent).order_by(Agent.name).limit(MAX_AGENTS_LISTED)
            )
            agents = list(result.scalars().all())
            # Detached, so working out who is offline below cannot be saved.
            session.expunge_all()
        if not agents:
            return "No agents are registered."
        lines = []
        for agent in agents:
            _normalize_status(agent)
            state = agent.status if agent.enabled else "disabled"
            labels = ", ".join(str(label) for label in agent.labels or []) or "none"
            lines.append(
                f"- {agent.name}: os={agent.os or 'unknown'}, "
                f"arch={agent.arch or 'unknown'}, labels={labels}, {state}"
            )
        return "\n".join(lines)

    return list_agents


async def _prepare_job(
    body: AssistantRequest,
    db: AsyncSession,
    current_user: User,
) -> _Job:
    """Shared by both assistant endpoints: check the AI configuration and
    build the conversations for a request."""
    from app.api.v1.system import get_ai_overrides, resolve_ai_config

    overrides = await get_ai_overrides(db)
    ai_cfg = resolve_ai_config(overrides)

    logger.info(
        "AI assistant request — provider=%s model=%s reasoning_model=%s "
        "base_url=%s enabled=%s has_key=%s",
        ai_cfg["provider"],
        ai_cfg["model"],
        ai_cfg.get("reasoning_model"),
        ai_cfg["base_url"] or "(default)",
        ai_cfg["enabled"],
        bool(ai_cfg["api_key"]),
    )

    if not ai_cfg["enabled"]:
        logger.warning("AI assistant is disabled, returning 503")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is disabled",
        )
    if not ai_cfg["api_key"] and ai_cfg["provider"] in ("openai", "anthropic", "azure_openai"):
        logger.warning("AI API key missing for provider=%s", ai_cfg["provider"])
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI API key is not configured",
        )

    if body.current_yaml and len(body.current_yaml.encode("utf-8")) > MAX_DOCUMENT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"The pipeline YAML is larger than {MAX_DOCUMENT_BYTES // 1024} KiB.",
        )

    # Appended to the system prompt in both modes.
    context = ""

    if body.project_id:
        # Resolve project_id for scoped secrets.read check.
        try:
            _pid_for_secrets = uuid.UUID(body.project_id)
        except ValueError:
            _pid_for_secrets = None
        can_read_secrets = (
            _pid_for_secrets is not None
            and (
                current_user.is_admin
                or "secrets.read" in effective_scoped_permissions(current_user, "project", _pid_for_secrets)
            )
        )
        project_ctx = await _build_project_context(
            db, body.project_id, include_values=can_read_secrets,
        )
        if project_ctx:
            context += (
                "\n\n## Project Context\n"
                "The user's project has the following secrets and variables "
                "configured. Use these exact names in the generated YAML "
                "instead of generic placeholders.\n\n"
                + project_ctx
            )

    # ----- Repository & Branch context -----
    repo_ctx = await _build_repo_context(
        db,
        repo_url=body.repo_url,
        branch=body.branch,
        pipeline_id=body.pipeline_id,
    )
    if repo_ctx:
        context += repo_ctx

    model_id = _build_model_id(ai_cfg)

    logger.info(
        "Sending AI request — model_id=%s provider=%s history=%d base_url=%s",
        model_id,
        ai_cfg["provider"],
        len(body.history or []),
        ai_cfg["base_url"] or "(default)",
    )

    return _Job(
        model_id=model_id,
        ai_cfg=ai_cfg,
        tool_messages=_tool_messages(body, TOOL_SYSTEM_PROMPT + context),
        legacy_messages=_legacy_messages(body, SYSTEM_PROMPT + context),
        current_yaml=body.current_yaml,
        list_agents=_agent_lister() if has_global_permission(current_user, "agents.read") else None,
    )


async def _check_ai_access(
    body: AssistantRequest,
    db: AsyncSession,
    current_user: User,
) -> None:
    """Enforce project-scoped or global pipelines.manage permission.

    - If the request carries a ``project_id``: require scoped ``pipelines.manage``
      for that project.
    - If the request carries a ``pipeline_id`` (but no ``project_id``): resolve
      the pipeline's project and check scoped access.
    - If neither is present: require global ``pipelines.manage``.
    """
    # Try to resolve a project_id from the request.
    resolved_pid: uuid.UUID | None = None

    if body.project_id:
        try:
            resolved_pid = uuid.UUID(body.project_id)
        except ValueError:
            pass

    if resolved_pid is None and body.pipeline_id:
        try:
            pl_id = uuid.UUID(body.pipeline_id)
            resolved_pid = await project_id_for_pipeline(db, pl_id)
        except ValueError:
            pass

    if resolved_pid is not None:
        check_scoped_permission(current_user, "pipelines.manage", "project", resolved_pid)
    else:
        # No project context — require a global grant.
        if not has_global_permission(current_user, "pipelines.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission 'pipelines.manage' required",
            )


def _model_options(job: _Job) -> dict[str, Any]:
    ai_cfg = job.ai_cfg
    return {
        "model": job.model_id,
        "temperature": 0.3,
        "timeout": 120,
        "api_key": str(ai_cfg["api_key"]) if ai_cfg["api_key"] else None,
        "api_base": str(ai_cfg["base_url"]) if ai_cfg["base_url"] else None,
    }


def _count_tokens(response: Any, usage: dict[str, int]) -> None:
    total = getattr(getattr(response, "usage", None), "total_tokens", None)
    if isinstance(total, int):
        usage["tokens"] += total


def _tool_model_id(job: _Job) -> str:
    """The model id for a call that carries tools.

    LiteLLM's ``ollama/`` provider cannot pass tools on: it switches the model
    to JSON-only output instead, for that call and for the process. Its
    ``ollama_chat/`` provider passes them, and Ollama rejects a model without
    tool support, which sends the request down the path without tools.
    """
    if job.model_id.startswith("ollama/"):
        return "ollama_chat/" + job.model_id.removeprefix("ollama/")
    return job.model_id


def _tool_completer(job: _Job, usage: dict[str, int]) -> Callable[[list[Any]], Awaitable[ModelTurn]]:
    """One model call with the tools attached, as the loop wants it."""
    options = {**_model_options(job), "model": _tool_model_id(job)}

    async def complete(messages: list[Any]) -> ModelTurn:
        response = await litellm.acompletion(
            messages=messages,
            tools=TOOL_DEFINITIONS,
            tool_choice="auto",
            **options,
        )
        _count_tokens(response, usage)
        message = response.choices[0].message
        calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments)
            for call in getattr(message, "tool_calls", None) or []
        ]
        # The provider's own message object goes back into the conversation
        # unchanged, so anything it needs to see again (ids, reasoning) is kept.
        return ModelTurn(text=message.content, tool_calls=calls, message=message)

    return complete


async def _legacy_reply(job: _Job, usage: dict[str, int]) -> str:
    """One model call without tools; the YAML comes back inside the reply."""
    response = await litellm.acompletion(messages=job.legacy_messages, **_model_options(job))
    _count_tokens(response, usage)
    return (response.choices[0].message.content or "").strip()


_YAML_BLOCK = re.compile(r"```(?:ya?ml)?\s*\n.*?```", re.DOTALL)


def _is_whole_pipeline(text: str) -> bool:
    """True for a full pipeline, false for a snippet shown in an answer: only
    a full pipeline may replace the editor's content."""
    return re.search(r"(?m)^stages\s*:", text) is not None


def _pipeline_name(text: str) -> str | None:
    for line in text.splitlines():
        key, colon, value = line.partition(":")
        if colon and key.rstrip() == "name":
            return value.strip().strip("\"'") or None
    return None


def _same_pipeline(current: str, candidate: str) -> bool:
    """True when *candidate* reads as a new version of the editor's pipeline
    rather than an example of some other one: the editor has no named
    pipeline yet, or both carry the same name."""
    name = _pipeline_name(current)
    return name is None or name == _pipeline_name(candidate)


def _without_yaml_block(reply: str, yaml_text: str) -> str:
    """The reply with its YAML removed, once that YAML is shown as a diff."""
    rest = _YAML_BLOCK.sub("", reply, count=1).strip()
    return "" if rest == yaml_text else rest


def _default_reply(changed: bool, limit_reached: bool, failed: bool) -> str:
    if failed:
        # Only reached with changes made; without any, the request is retried.
        return (
            "The AI provider stopped responding before I finished. The changes I "
            "made so far are below — check them before applying."
        )
    if limit_reached and changed:
        return (
            "I ran out of steps before finishing. The changes I made so far are "
            "below — check them before applying."
        )
    if limit_reached:
        return "I ran out of steps before making a change. Try a more specific request."
    if changed:
        return "I updated the pipeline. Review the changes below."
    return "I could not produce an answer. Please try again."


async def _answer(job: _Job, on_step: OnStep | None = None) -> AssistantResponse:
    """Answer one request: with tools when the model accepts them, otherwise
    with a single call whose reply carries the YAML.

    Raises whatever the AI provider raised when no answer could be had.
    """
    document = WorkingDocument(job.current_yaml)
    ctx = ToolContext(document=document, topics=REFERENCE_TOPICS, list_agents=job.list_agents)
    usage = {"tokens": 0}
    mode = "tools"
    result = LoopResult()
    reply = ""
    failure: Exception | None = None

    try:
        result = await run_loop(job.tool_messages, _tool_completer(job, usage), ctx, on_step=on_step)
        reply = result.reply
    except Exception as exc:
        failure = exc
    else:
        # A later model call failed. Changes already made are kept and
        # proposed; with none, nothing is lost by handling it like a failure
        # of the first call.
        if result.error is not None and not document.changed:
            failure = result.error

    if failure is not None:
        if isinstance(failure, (
            litellm.exceptions.AuthenticationError,
            litellm.exceptions.APIConnectionError,
            litellm.exceptions.Timeout,
            litellm.exceptions.RateLimitError,
        )):
            # A call without tools would fail the same way.
            raise failure
        # Most often a model or endpoint that does not accept tools.
        logger.warning(
            "AI call with tools failed (%s: %s) — retrying without tools",
            type(failure).__name__, failure,
        )
        mode = "legacy"
        reply = await _legacy_reply(job, usage)

    stopped_early = mode == "tools" and result.limit_reached
    provider_failed = mode == "tools" and result.error is not None

    if not document.changed:
        # Without tools — or with a model that ignored them — the whole
        # pipeline is in the reply. Turn it into the same proposal. A model
        # that has tools may also be answering a question with an example of
        # some other pipeline: that is not a change to this one, and its
        # reply is never cut.
        candidate = _extract_yaml(reply)
        if candidate and _is_whole_pipeline(candidate) and (
            mode == "legacy" or _same_pipeline(document.text, candidate)
        ):
            try:
                document.write(candidate)
            except DocumentError as exc:
                logger.warning("AI reply YAML not usable as a proposal — %s", exc)
            else:
                if mode == "legacy":
                    reply = _without_yaml_block(reply, candidate)

    proposal = None
    if document.changed:
        problems = [
            {"message": problem.message, "line": problem.line}
            for problem in validate_pipeline_definition(document.text)
        ]
        built = build_proposal(document.original, document.text, problems)
        if built is not None:
            proposal = AssistantProposal.model_validate(built.to_dict())

    logger.info(
        "AI assistant response — mode=%s model_calls=%d tool_calls=%d tokens=%d "
        "limit_reached=%s proposal=%s",
        mode, result.model_calls, len(result.steps), usage["tokens"],
        stopped_early, proposal is not None,
    )

    return AssistantResponse(
        reply=reply or _default_reply(proposal is not None, stopped_early, provider_failed),
        yaml=proposal.yaml if proposal else None,
        mode=mode,
        limit_reached=stopped_early,
        steps=[AssistantStep(tool=s.tool, label=s.label, ok=s.ok) for s in result.steps],
        proposal=proposal,
    )


def _provider_error_detail(exc: Exception) -> str:
    """Log a failed assistant request and describe it for the user."""
    if isinstance(exc, litellm.exceptions.AuthenticationError):
        logger.error("AI provider authentication failed — %s", exc)
        return f"AI provider authentication failed: {exc.message}"
    if isinstance(exc, litellm.exceptions.BadRequestError):
        logger.error("AI provider bad request — %s", exc)
        return f"AI provider rejected request: {exc.message}"
    if isinstance(exc, litellm.exceptions.APIConnectionError):
        logger.error("AI provider unreachable — %s", exc)
        return f"AI provider unreachable: {exc.message}"
    if isinstance(exc, (AttributeError, IndexError, TypeError)):
        logger.error("Failed to parse AI response — %s", exc)
        return f"Unexpected AI provider response format: {exc}"
    logger.error("AI provider error — %s: %s", type(exc).__name__, exc)
    return f"AI provider error: {exc}"


@router.post("/assistant", response_model=AssistantResponse)
async def pipeline_assistant(
    body: AssistantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> AssistantResponse:
    await _check_ai_access(body, db, current_user)
    job = await _prepare_job(body, db, current_user)
    # The model can take minutes. Nothing below needs the database, so its
    # connection goes back to the pool now.
    await db.close()

    try:
        return await _answer(job)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=_provider_error_detail(exc),
        )


# Proxies close a connection that stays silent; a long model call is silent.
KEEPALIVE_SECONDS = 15.0


def _event(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_events(job: _Job) -> AsyncIterator[str]:
    """Server-sent events for one request: a ``step`` after each tool call,
    then one ``done`` (the response) or one ``error``."""
    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def on_step(step: Step) -> None:
        await queue.put(_event({"type": "step", "tool": step.tool, "label": step.label, "ok": step.ok}))

    async def work() -> None:
        try:
            response = await _answer(job, on_step)
            await queue.put(_event({"type": "done", **response.model_dump()}))
        except Exception as exc:
            await queue.put(_event({"type": "error", "detail": _provider_error_detail(exc)}))
        finally:
            await queue.put(None)

    task = asyncio.create_task(work())
    waiting = asyncio.ensure_future(queue.get())
    try:
        while True:
            done, _ = await asyncio.wait({waiting}, timeout=KEEPALIVE_SECONDS)
            if not done:
                yield ": keep-alive\n\n"
                continue
            item = waiting.result()
            if item is None:
                break
            yield item
            waiting = asyncio.ensure_future(queue.get())
    finally:
        # Reached early when the client goes away: stop paying for the model.
        waiting.cancel()
        task.cancel()


@router.post("/assistant/stream")
async def pipeline_assistant_stream(
    body: AssistantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    await _check_ai_access(body, db, current_user)
    job = await _prepare_job(body, db, current_user)
    # As above: the connection is not held while the reply is streamed.
    await db.close()
    return StreamingResponse(
        _stream_events(job),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _extract_yaml(text: str) -> str | None:
    """Try to pull a YAML code block out of the AI response.

    If the entire response looks like bare YAML (starts with a YAML key or
    ``version:``), return it as-is.
    """
    import re

    match = re.search(r"```(?:ya?ml)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()

    stripped = text.strip()
    if stripped.startswith("version:") or stripped.startswith("stages:") or stripped.startswith("name:"):
        return stripped

    return None
