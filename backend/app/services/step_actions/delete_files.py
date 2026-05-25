"""
Handler for ``delete_files`` steps.

Deletes files or directories at the specified path(s).
The actual deletion is performed by the agent; this handler only provides validation.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult


class DeleteFilesHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        yield StepResult(
            exit_code=1,
            status="failed",
            error="delete_files steps must be executed by an agent",
        )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        path = config.get("path")
        paths = config.get("paths")
        if not path and not paths:
            errors.append("'delete_files' requires 'path' (string) or 'paths' (list)")
        if path and not isinstance(path, str):
            errors.append("'delete_files' 'path' must be a string")
        if paths is not None:
            if not isinstance(paths, list):
                errors.append("'delete_files' 'paths' must be a list of strings")
            elif not all(isinstance(p, str) for p in paths):
                errors.append("'delete_files' 'paths' entries must be strings")
        return errors


register("delete_files", DeleteFilesHandler())
