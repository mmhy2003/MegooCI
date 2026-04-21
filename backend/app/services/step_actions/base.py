"""
Base class and types for pipeline step action handlers.

Every concrete step type (shell, docker_build, ssh_exec, ...) implements
the ``StepActionHandler`` interface.  The build executor uses the registry
in ``__init__.py`` to dispatch each step to the right handler.
"""

from __future__ import annotations

import abc
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class StepContext:
    """Runtime context passed to every handler invocation."""

    build_id: uuid.UUID
    step_id: uuid.UUID
    step_name: str
    stage_name: str
    pipeline_id: uuid.UUID
    project_id: uuid.UUID
    branch: str | None = None
    commit_sha: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    workspace_dir: str | None = None


@dataclass
class LogLine:
    """A single line of output produced by a handler."""

    stream: str  # "stdout" | "stderr" | "system"
    content: str


@dataclass
class StepResult:
    """Terminal outcome of a step execution."""

    exit_code: int
    status: str  # "success" | "failed" | "cancelled"
    error: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)


class StepActionHandler(abc.ABC):
    """Interface that every step type must implement.

    ``execute`` is an async generator that yields ``LogLine`` objects while the
    action runs and finally returns a ``StepResult``.  The executor consumes
    the generator, persists each log line, and uses the final result to update
    the Step row.
    """

    @abc.abstractmethod
    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        """Yield LogLine objects during execution, then yield a final StepResult."""
        ...  # pragma: no cover
        # Make the linter happy — abstract async generators need this:
        if False:
            yield  # type: ignore[misc]

    @abc.abstractmethod
    def validate_config(self, config: dict[str, Any]) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        ...
