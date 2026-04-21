"""
Handlers for Git-related step types:
  - git_clone  — clone a repository into the workspace
  - git_pull   — pull latest changes
  - git_push   — push commits to a remote

YAML examples:

  - git_clone:
      repo: "https://github.com/org/repo.git"
      branch: main
      depth: 1
      path: "."

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
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult


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

        token = ctx.secrets.get("GIT_TOKEN", "")
        if token:
            repo = _inject_token_into_url(repo, token)

        args = ["git", "clone"]
        if branch:
            args += ["-b", branch]
        if depth:
            args += ["--depth", str(depth)]
        args += [repo, path]

        async for item in _run_git_cmd(args, ctx):
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
