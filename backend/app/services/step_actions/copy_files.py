"""
Handler for ``copy_files`` steps.

Copies files or directories from a source path to a destination path.
The actual copy is performed by the agent; this handler only provides validation.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult


class CopyFilesHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        yield StepResult(
            exit_code=1,
            status="failed",
            error="copy_files steps must be executed by an agent",
        )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("source"):
            errors.append("'copy_files' requires 'source'")
        elif not isinstance(config["source"], str):
            errors.append("'copy_files' 'source' must be a string")
        if not config.get("destination"):
            errors.append("'copy_files' requires 'destination'")
        elif not isinstance(config["destination"], str):
            errors.append("'copy_files' 'destination' must be a string")
        return errors


register("copy_files", CopyFilesHandler())
