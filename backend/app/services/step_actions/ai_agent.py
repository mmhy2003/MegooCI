"""
Handler for ``ai_agent`` steps.

Runs the Pi AI coding agent with a prompt. The actual execution
is performed by the agent (Go); this handler only provides validation.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult


class AiAgentHandler(StepActionHandler):

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        yield StepResult(
            exit_code=1,
            status="failed",
            error="ai_agent steps must be executed by an agent",
        )

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("prompt"):
            errors.append("'ai_agent' requires 'prompt'")
        elif not isinstance(config["prompt"], str):
            errors.append("'ai_agent' 'prompt' must be a string")
        if not config.get("api_key"):
            errors.append("'ai_agent' requires 'api_key'")
        elif not isinstance(config["api_key"], str):
            errors.append("'ai_agent' 'api_key' must be a string")
        timeout = config.get("timeout")
        if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
            errors.append("'ai_agent' 'timeout' must be a positive number")
        return errors


register("ai_agent", AiAgentHandler())
