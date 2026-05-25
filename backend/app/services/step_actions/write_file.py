"""
Handler for ``write_file`` steps.

Writes content to a file at a specified path. The actual file creation
is performed by the agent; this handler only provides validation.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult


class WriteFileHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        # write_file steps are executed entirely by the agent.
        # If we ever reach this server-side path, something is wrong.
        yield StepResult(
            exit_code=1,
            status="failed",
            error="write_file steps must be executed by an agent",
        )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("path"):
            errors.append("'write_file' requires 'path'")
        elif not isinstance(config["path"], str):
            errors.append("'write_file' 'path' must be a string")
        if "content" not in config:
            errors.append("'write_file' requires 'content'")
        elif not isinstance(config.get("content", ""), str):
            errors.append("'write_file' 'content' must be a string")
        return errors


register("write_file", WriteFileHandler())
