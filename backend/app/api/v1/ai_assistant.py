"""
AI pipeline assistant — generates or modifies megooci.yaml content based on
natural language prompts.

Uses the OpenAI-compatible chat API configured via MEGOOCI_AI_* settings.
Supports any provider that speaks the OpenAI chat format (OpenAI, Azure,
Ollama, vLLM, etc.) by setting MEGOOCI_AI_BASE_URL.
"""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import require_permission
from app.database import get_db
from app.models.secret import EnvVar, Secret
from app.models.user import User

router = APIRouter()

SYSTEM_PROMPT = """\
You are MegooCI Pipeline Assistant — an expert at writing CI/CD pipeline \
definitions in YAML for the MegooCI platform.

## Available Step Types

### run — Execute shell commands
```yaml
- run: "npm install && npm run build"
```

### docker_login — Authenticate with a container registry
```yaml
- docker_login:
    registry: ghcr.io
    username: ${{ secrets.GHCR_USER }}
    password: ${{ secrets.GHCR_TOKEN }}
```

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

### git_clone — Clone a repository
```yaml
- git_clone:
    repo: "https://github.com/org/repo.git"
    branch: main
    depth: 1     # optional shallow clone
    path: "."    # optional checkout path
```

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
```

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

## Pipeline Structure
```yaml
version: 1
name: pipeline-name
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
```

## Placeholders
- `${{ secrets.NAME }}` — replaced at runtime with decrypted project/pipeline secrets
- `${{ env.NAME }}` — replaced at runtime with environment variables

## Rules
1. Always output valid YAML.
2. When the user asks to generate a pipeline, output ONLY the YAML with no \
markdown fences, no explanations before or after.
3. When the user asks a question about syntax, answer concisely and include a \
short YAML example.
4. Use realistic, production-quality examples.
5. Always use `${{ secrets.X }}` for sensitive values — never hardcode passwords.
6. For notification steps, always use a configured channel name — never hardcode \
webhook URLs or bot tokens in the YAML.
"""


class ChatMessage(BaseModel):
    role: str  # "user" | "assistant"
    content: str


class AssistantRequest(BaseModel):
    prompt: str
    current_yaml: str | None = None
    project_id: str | None = None
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


@router.post("/assistant", response_model=AssistantResponse)
async def pipeline_assistant(
    body: AssistantRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("pipelines.manage")),
) -> AssistantResponse:
    from app.core.deps import _collect_permissions

    settings = get_settings()

    if not settings.MEGOOCI_AI_ENABLED:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI assistant is disabled",
        )
    if not settings.MEGOOCI_AI_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI API key is not configured",
        )

    user_perms = _collect_permissions(current_user)
    can_read_secrets = current_user.is_admin or "secrets.read" in user_perms

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

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_content},
    ]

    if body.current_yaml:
        messages.append({
            "role": "user",
            "content": f"Here is my current pipeline YAML:\n```yaml\n{body.current_yaml}\n```",
        })
        messages.append({
            "role": "assistant",
            "content": "I can see your current pipeline. What would you like me to do with it?",
        })

    if body.history:
        for msg in body.history:
            if msg.role in ("user", "assistant"):
                messages.append({"role": msg.role, "content": msg.content})

    messages.append({"role": "user", "content": body.prompt})

    base_url = settings.MEGOOCI_AI_BASE_URL or "https://api.openai.com/v1"
    url = f"{base_url.rstrip('/')}/chat/completions"

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.MEGOOCI_AI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.MEGOOCI_AI_MODEL or "gpt-4o-mini",
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 4096,
                },
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI provider returned {exc.response.status_code}",
            )
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"AI provider unreachable: {exc}",
            )

    data = resp.json()
    reply_text = data["choices"][0]["message"]["content"].strip()

    yaml_block = _extract_yaml(reply_text)

    return AssistantResponse(reply=reply_text, yaml=yaml_block)


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
