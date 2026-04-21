"""
Handlers for Docker-related step types:
  - docker_login  — authenticate with a container registry
  - docker_build  — build an image from a Dockerfile
  - docker_push   — push an image to a registry

All three delegate to the Docker CLI (``docker``) via subprocess. The agent
or controller must have Docker installed and the daemon running.

YAML examples:

  - docker_login:
      registry: ghcr.io
      username: ${{ secrets.GHCR_USER }}
      password: ${{ secrets.GHCR_TOKEN }}

  - docker_build:
      context: "."
      dockerfile: Dockerfile
      tags:
        - "ghcr.io/myorg/myapp:latest"
        - "ghcr.io/myorg/myapp:${{ env.GIT_SHA }}"
      build_args:
        NODE_ENV: production

  - docker_push:
      tags:
        - "ghcr.io/myorg/myapp:latest"
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult


async def _run_docker_cmd(
    args: list[str],
    ctx: StepContext,
    *,
    stdin_data: str | None = None,
) -> AsyncIterator[LogLine | StepResult]:
    """Shared helper: run a ``docker …`` subprocess, stream output, yield result."""
    cmd_display = " ".join(args)
    yield LogLine(stream="system", content=f"$ {cmd_display}\n")

    env_pairs = {**ctx.env, **ctx.secrets}

    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
            env={**dict(__import__("os").environ), **env_pairs} if env_pairs else None,
            cwd=ctx.workspace_dir,
        )
    except FileNotFoundError:
        yield LogLine(stream="stderr", content="'docker' executable not found. Is Docker installed?\n")
        yield StepResult(exit_code=1, status="failed", error="docker not found")
        return
    except Exception as exc:
        yield LogLine(stream="stderr", content=f"Failed to start docker: {exc}\n")
        yield StepResult(exit_code=1, status="failed", error=str(exc))
        return

    if stdin_data:
        process.stdin.write(stdin_data.encode())  # type: ignore[union-attr]
        process.stdin.close()  # type: ignore[union-attr]

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
    yield StepResult(
        exit_code=exit_code,
        status="success" if exit_code == 0 else "failed",
    )


# ── docker_login ──────────────────────────────────────────────────────────


class DockerLoginHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        registry = config.get("registry", "")
        username = config.get("username", "")
        password = config.get("password", "")

        args = ["docker", "login"]
        if username:
            args += ["-u", username]
        args += ["--password-stdin"]
        if registry:
            args.append(registry)

        async for item in _run_docker_cmd(args, ctx, stdin_data=password):
            yield item

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("password") and not config.get("username"):
            errors.append("docker_login requires at least 'username' and 'password'")
        return errors


# ── docker_build ──────────────────────────────────────────────────────────


class DockerBuildHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        context_path = config.get("context", ".")
        dockerfile = config.get("dockerfile")
        tags: list[str] = config.get("tags", [])
        build_args: dict[str, str] = config.get("build_args", {})
        target = config.get("target")
        no_cache = config.get("no_cache", False)
        platform = config.get("platform")

        args = ["docker", "build"]
        for tag in tags:
            args += ["-t", tag]
        if dockerfile:
            args += ["-f", dockerfile]
        if target:
            args += ["--target", target]
        if no_cache:
            args.append("--no-cache")
        if platform:
            args += ["--platform", platform]
        for k, v in build_args.items():
            args += ["--build-arg", f"{k}={v}"]
        args.append(context_path)

        async for item in _run_docker_cmd(args, ctx):
            yield item

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        tags = config.get("tags", [])
        if not tags:
            errors.append("docker_build requires at least one tag in 'tags'")
        if not isinstance(tags, list):
            errors.append("docker_build 'tags' must be a list")
        return errors


# ── docker_push ───────────────────────────────────────────────────────────


class DockerPushHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        tags: list[str] = config.get("tags", [])
        if not tags:
            image = config.get("image", "")
            if image:
                tags = [image]

        last_result: StepResult | None = None
        for tag in tags:
            args = ["docker", "push", tag]
            async for item in _run_docker_cmd(args, ctx):
                if isinstance(item, StepResult):
                    last_result = item
                    if item.status == "failed":
                        yield item
                        return
                else:
                    yield item

        yield last_result or StepResult(exit_code=0, status="success")

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        tags = config.get("tags", [])
        image = config.get("image")
        if not tags and not image:
            errors.append("docker_push requires 'tags' (list) or 'image' (string)")
        return errors


register("docker_login", DockerLoginHandler())
register("docker_build", DockerBuildHandler())
register("docker_push", DockerPushHandler())
