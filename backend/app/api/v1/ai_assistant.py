"""
AI pipeline assistant — generates or modifies megooci.yaml content based on
natural language prompts.

Uses LiteLLM for unified multi-provider LLM support. LiteLLM automatically
handles provider-specific parameter translation (reasoning models, Anthropic
native API, Azure, Ollama, etc.) via a single ``completion()`` interface.
"""

from __future__ import annotations

import json
import logging
import uuid

logger = logging.getLogger("uvicorn.error")

import litellm
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import require_permission
from app.database import get_db
from app.models.git_integration import ProjectRepository
from app.models.pipeline import Pipeline
from app.models.secret import EnvVar, Secret
from app.models.user import User

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
timeout. Directory entries in `manifests` are applied non-recursively.

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
      Commit: ${{ build.commit_sha }}
    subject: "Build Report"    # optional (used by email channels)
    recipient: "#deployments"  # optional channel/chat_id/email override
```

Supported channel types: email (SMTP), Slack (webhook), Telegram (bot).
Channels are configured by admins in the Notification Channels UI.

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

## Pipeline Structure
```yaml
version: 1
name: pipeline-name
runs_on: linux          # optional — target a specific agent environment
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
7. For notification steps, always use a configured channel name — never hardcode \
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
"""


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


class AssistantResponse(BaseModel):
    reply: str
    yaml: str | None = None


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


async def _prepare_messages(
    body: AssistantRequest,
    db: AsyncSession,
    current_user: User,
) -> tuple[list[dict[str, str]], str, dict]:
    """Shared logic for both the regular and streaming assistant endpoints.

    Returns ``(messages, model_id, ai_cfg)``.
    """
    from app.core.deps import _collect_permissions
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

    user_perms = _collect_permissions(current_user)
    can_read_secrets = "admin" in user_perms or "secrets.read" in user_perms

    system_content = SYSTEM_PROMPT

    if body.project_id:
        project_ctx = await _build_project_context(
            db, body.project_id, include_values=can_read_secrets,
        )
        if project_ctx:
            system_content += (
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
        system_content += repo_ctx

    model_id = _build_model_id(ai_cfg)

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_content},
    ]

    if body.history:
        for msg in body.history:
            if msg.role in ("user", "assistant"):
                messages.append({"role": msg.role, "content": msg.content})

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

    logger.info(
        "Sending AI request — model_id=%s provider=%s message_count=%d "
        "base_url=%s",
        model_id,
        ai_cfg["provider"],
        len(messages),
        ai_cfg["base_url"] or "(default)",
    )

    return messages, model_id, ai_cfg


@router.post("/assistant", response_model=AssistantResponse)
async def pipeline_assistant(
    body: AssistantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("pipelines.manage")),
) -> AssistantResponse:
    messages, model_id, ai_cfg = await _prepare_messages(body, db, current_user)

    try:
        response = await litellm.acompletion(
            model=model_id,
            messages=messages,
            temperature=0.3,
            timeout=120,
            api_key=str(ai_cfg["api_key"]) if ai_cfg["api_key"] else None,
            api_base=str(ai_cfg["base_url"]) if ai_cfg["base_url"] else None,
        )
    except litellm.exceptions.AuthenticationError as exc:
        logger.error("AI provider authentication failed — %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider authentication failed: {exc.message}",
        )
    except litellm.exceptions.BadRequestError as exc:
        logger.error("AI provider bad request — %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider rejected request: {exc.message}",
        )
    except litellm.exceptions.APIConnectionError as exc:
        logger.error("AI provider unreachable — %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider unreachable: {exc.message}",
        )
    except Exception as exc:
        logger.error("AI provider error — %s: %s", type(exc).__name__, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"AI provider error: {exc}",
        )

    try:
        reply_text = response.choices[0].message.content.strip()
    except (AttributeError, IndexError, TypeError) as exc:
        logger.error(
            "Failed to parse AI response — error=%s response=%s",
            exc,
            str(response)[:2000],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Unexpected AI provider response format: {exc}",
        )

    logger.info(
        "AI assistant response — reply_length=%d usage=%s has_yaml=%s",
        len(reply_text),
        getattr(response, "usage", None),
        bool(_extract_yaml(reply_text)),
    )

    yaml_block = _extract_yaml(reply_text)

    return AssistantResponse(reply=reply_text, yaml=yaml_block)


async def _stream_generator(messages, model_id, ai_cfg):
    """Async generator that yields SSE events for the streaming endpoint."""
    try:
        response = await litellm.acompletion(
            model=model_id,
            messages=messages,
            temperature=0.3,
            stream=True,
            timeout=120,
            api_key=str(ai_cfg["api_key"]) if ai_cfg["api_key"] else None,
            api_base=str(ai_cfg["base_url"]) if ai_cfg["base_url"] else None,
        )
        full_reply = ""
        async for chunk in response:
            delta = chunk.choices[0].delta.content
            if delta:
                full_reply += delta
                yield f"data: {json.dumps({'type': 'token', 'content': delta})}\n\n"

        yaml_block = _extract_yaml(full_reply)
        yield f"data: {json.dumps({'type': 'done', 'reply': full_reply.strip(), 'yaml': yaml_block})}\n\n"
    except Exception as exc:
        logger.error("AI streaming error — %s: %s", type(exc).__name__, exc)
        yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"


@router.post("/assistant/stream")
async def pipeline_assistant_stream(
    body: AssistantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("pipelines.manage")),
):
    messages, model_id, ai_cfg = await _prepare_messages(body, db, current_user)
    return StreamingResponse(
        _stream_generator(messages, model_id, ai_cfg),
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
