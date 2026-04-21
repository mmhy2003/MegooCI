"""
AI pipeline assistant — generates or modifies megooci.yaml content based on
natural language prompts.

Uses the OpenAI-compatible chat API configured via MEGOOCI_AI_* settings.
Supports any provider that speaks the OpenAI chat format (OpenAI, Azure,
Ollama, vLLM, etc.) by setting MEGOOCI_AI_BASE_URL.
"""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import get_current_active_user
from app.database import get_db
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
"""


class AssistantRequest(BaseModel):
    prompt: str
    current_yaml: str | None = None


class AssistantResponse(BaseModel):
    reply: str
    yaml: str | None = None


@router.post("/assistant", response_model=AssistantResponse)
async def pipeline_assistant(
    body: AssistantRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> AssistantResponse:
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

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    if body.current_yaml:
        messages.append({
            "role": "user",
            "content": f"Here is my current pipeline YAML:\n```yaml\n{body.current_yaml}\n```",
        })
        messages.append({
            "role": "assistant",
            "content": "I can see your current pipeline. What would you like me to do with it?",
        })

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
