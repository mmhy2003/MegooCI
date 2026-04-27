"""
Handlers for Git-related step types:
  - git_clone  — clone a repository into the workspace
  - git_pull   — pull latest changes
  - git_push   — push commits to a remote

YAML examples:

  # Public repo
  - git_clone:
      repo: "https://github.com/org/repo.git"
      branch: main
      depth: 1
      path: "."

  # Private repo — explicit token via secret interpolation
  - git_clone:
      repo: "https://github.com/org/private-repo.git"
      token: ${{ secrets.GIT_TOKEN }}
      branch: main

  # Private repo — auto-injects token from connected Git provider
  - git_clone:
      repo: "https://github.com/org/private-repo.git"
      branch: main

  - git_pull:
      remote: origin
      branch: main

  - git_push:
      remote: origin
      branch: main
      force: false
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult

logger = logging.getLogger(__name__)


async def _run_git_cmd(
    args: list[str],
    ctx: StepContext,
) -> AsyncIterator[LogLine | StepResult]:
    cmd_display = " ".join(args)
    yield LogLine(stream="system", content=f"$ {cmd_display}\n")

    env_pairs = {**ctx.env}
    if ctx.secrets.get("GIT_TOKEN"):
        env_pairs["GIT_ASKPASS"] = "echo"
        env_pairs["GIT_TERMINAL_PROMPT"] = "0"

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**dict(__import__("os").environ), **env_pairs} if env_pairs else None,
            cwd=ctx.workspace_dir,
        )
    except FileNotFoundError:
        yield LogLine(stream="stderr", content="'git' executable not found.\n")
        yield StepResult(exit_code=1, status="failed", error="git not found")
        return
    except Exception as exc:
        yield LogLine(stream="stderr", content=f"Failed to start git: {exc}\n")
        yield StepResult(exit_code=1, status="failed", error=str(exc))
        return

    lines: list[LogLine] = []

    async def _read(stream: asyncio.StreamReader, name: str) -> None:
        async for raw in stream:
            lines.append(LogLine(stream=name, content=raw.decode(errors="replace")))

    t1 = asyncio.create_task(_read(process.stdout, "stdout"))  # type: ignore[arg-type]
    t2 = asyncio.create_task(_read(process.stderr, "stderr"))  # type: ignore[arg-type]

    while not t1.done() or not t2.done():
        await asyncio.sleep(0.05)
        while lines:
            yield lines.pop(0)

    await asyncio.gather(t1, t2)
    while lines:
        yield lines.pop(0)

    await process.wait()
    exit_code = process.returncode or 0
    yield StepResult(exit_code=exit_code, status="success" if exit_code == 0 else "failed")


def _inject_token_into_url(repo_url: str, token: str) -> str:
    """Inject a token into an HTTPS git URL for authentication.

    ``https://github.com/org/repo`` → ``https://x-access-token:{token}@github.com/org/repo``
    """
    if not token or not repo_url.startswith("https://"):
        return repo_url
    return repo_url.replace("https://", f"https://x-access-token:{token}@", 1)


async def _resolve_git_token(
    repo_url: str,
    config: dict[str, Any],
    ctx: StepContext,
    db: AsyncSession,
) -> str:
    """Resolve a Git authentication token using three strategies in priority order:

    1. Explicit ``token`` field in the step config (supports secret interpolation).
    2. ``GIT_TOKEN`` secret defined in the pipeline/project scope.
    3. Auto-inject from a matching ``GitProviderConnection`` linked to the project.
    """
    # Strategy 1: explicit token from YAML config
    explicit_token = config.get("token", "")
    if explicit_token:
        return explicit_token

    # Strategy 2: GIT_TOKEN from project/pipeline secrets
    secret_token = ctx.secrets.get("GIT_TOKEN", "")
    if secret_token:
        return secret_token

    # Strategy 3: auto-inject from connected Git provider
    return await _lookup_provider_token(repo_url, ctx.project_id, db)


async def _lookup_provider_token(
    repo_url: str,
    project_id: Any,
    db: AsyncSession,
) -> str:
    """Find a matching GitProviderConnection for the repo URL and decrypt its token.

    Matches by comparing the hostname of the repo URL with the base_url of
    connected providers, or by checking ProjectRepository links for the project.
    """
    try:
        from app.models.git_integration import GitProviderConnection, ProjectRepository
        from app.core.security import decrypt_secret
        from app.config import get_settings

        parsed = urlparse(repo_url)
        repo_host = parsed.hostname or ""

        # First try: find a ProjectRepository linked to this project that
        # matches the repo URL (or at least the same host).
        result = await db.execute(
            select(ProjectRepository)
            .where(ProjectRepository.project_id == project_id)
        )
        for proj_repo in result.scalars().all():
            proj_host = urlparse(proj_repo.repo_url).hostname or ""
            if proj_host == repo_host or proj_repo.repo_url.rstrip("/") == repo_url.rstrip("/"):
                connection = await db.get(GitProviderConnection, proj_repo.connection_id)
                if connection and connection.encrypted_credential:
                    settings = get_settings()
                    return decrypt_secret(
                        connection.encrypted_credential,
                        settings.MEGOOCI_SECRET_KEY,
                    )

        # Fallback: find any connection whose base_url matches the repo host.
        result = await db.execute(select(GitProviderConnection))
        for conn in result.scalars().all():
            conn_host = ""
            if conn.base_url:
                conn_host = urlparse(conn.base_url).hostname or ""
            # Default host for known providers
            if not conn_host:
                if conn.provider_type == "github":
                    conn_host = "github.com"
                elif conn.provider_type == "gitlab":
                    conn_host = "gitlab.com"

            if conn_host and conn_host == repo_host and conn.encrypted_credential:
                settings = get_settings()
                return decrypt_secret(
                    conn.encrypted_credential,
                    settings.MEGOOCI_SECRET_KEY,
                )

    except Exception as exc:
        logger.debug("Auto-inject git token lookup failed: %s", exc)

    return ""


class GitCloneHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        repo = config.get("repo", "")
        branch = config.get("branch", ctx.branch or "main")
        depth = config.get("depth")
        path = config.get("path", ".")

        token = await _resolve_git_token(repo, config, ctx, db)
        if token:
            repo = _inject_token_into_url(repo, token)

        args = ["git", "clone"]
        if branch:
            args += ["-b", branch]
        if depth:
            args += ["--depth", str(depth)]
        args += [repo, path]

        async for item in _run_git_cmd(args, ctx):
            # Mask the token from log output
            if token and isinstance(item, LogLine) and token in item.content:
                item = LogLine(
                    stream=item.stream,
                    content=item.content.replace(token, "****"),
                )
            yield item

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("repo"):
            errors.append("git_clone requires a 'repo' URL")
        return errors


class GitPullHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        remote = config.get("remote", "origin")
        branch = config.get("branch", "")

        args = ["git", "pull", remote]
        if branch:
            args.append(branch)

        async for item in _run_git_cmd(args, ctx):
            yield item

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []


class GitPushHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        remote = config.get("remote", "origin")
        branch = config.get("branch", "")
        force = config.get("force", False)

        args = ["git", "push"]
        if force:
            args.append("--force")
        args.append(remote)
        if branch:
            args.append(branch)

        async for item in _run_git_cmd(args, ctx):
            yield item

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        return []


register("git_clone", GitCloneHandler())
register("git_pull", GitPullHandler())
register("git_push", GitPushHandler())
