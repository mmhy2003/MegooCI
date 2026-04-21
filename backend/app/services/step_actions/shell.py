"""
Handler for ``run`` (shell command) steps.

This is the original step type — wraps asyncio.create_subprocess_shell with
streaming log output.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult


class ShellHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        command = config.get("command", "")
        if not command:
            yield StepResult(exit_code=1, status="failed", error="Empty command")
            return

        env_pairs = {**ctx.env, **ctx.secrets}

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**dict(__import__("os").environ), **env_pairs} if env_pairs else None,
                cwd=ctx.workspace_dir,
            )
        except Exception as exc:
            yield LogLine(stream="stderr", content=f"Failed to start process: {exc}\n")
            yield StepResult(exit_code=1, status="failed", error=str(exc))
            return

        async def _read(stream: asyncio.StreamReader, name: str) -> None:
            async for raw in stream:
                line = raw.decode(errors="replace")
                lines_queue.append(LogLine(stream=name, content=line))

        lines_queue: list[LogLine] = []

        stdout_task = asyncio.create_task(_read(process.stdout, "stdout"))  # type: ignore[arg-type]
        stderr_task = asyncio.create_task(_read(process.stderr, "stderr"))  # type: ignore[arg-type]

        while not stdout_task.done() or not stderr_task.done():
            await asyncio.sleep(0.05)
            while lines_queue:
                yield lines_queue.pop(0)

        await asyncio.gather(stdout_task, stderr_task)
        while lines_queue:
            yield lines_queue.pop(0)

        await process.wait()
        exit_code = process.returncode or 0
        status = "success" if exit_code == 0 else "failed"
        yield StepResult(exit_code=exit_code, status=status)

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("command"):
            errors.append("'run' step requires a 'command' (the shell command to execute)")
        return errors


register("run", ShellHandler())
