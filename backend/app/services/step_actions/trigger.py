"""Trigger-pipeline step action handler.

Triggers another pipeline's build from within a running pipeline. Always
executed server-side because it needs DB access to resolve the target
pipeline and enqueue the build.

YAML syntax:
    - trigger_pipeline:
        pipeline: "deploy-production"   # target pipeline name or UUID
        branch: main                    # optional (defaults to target's default_branch)
        params:                         # optional parameters forwarded to the child build
          VERSION: "1.2.3"
        wait: true                      # optional — wait for triggered build to finish (default: false)
        timeout: 3600                   # optional — max seconds to wait (default: 3600, only used when wait=true)
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.build import Build, Stage, Step
from app.models.pipeline import Pipeline
from app.services.pipeline_compiler import compile_to_build_graph, parse_yaml_pipeline
from app.tasks.build_tasks import run_build

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult

_DEFAULT_TIMEOUT = 3600
_POLL_INTERVAL = 5


def _resolve_pipeline_id(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


class TriggerPipelineHandler(StepActionHandler):
    """Triggers another pipeline and optionally waits for it to complete."""

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        pipeline_ref = config.get("pipeline", "")
        branch = config.get("branch")
        params: dict[str, Any] = config.get("params", {})
        wait = config.get("wait", False)
        timeout = config.get("timeout", _DEFAULT_TIMEOUT)

        if not pipeline_ref:
            yield LogLine(stream="stderr", content="Error: 'pipeline' is required in trigger_pipeline step\n")
            yield StepResult(exit_code=1, status="failed", error="Missing 'pipeline'")
            return

        target_id = _resolve_pipeline_id(str(pipeline_ref))
        if target_id:
            target = await db.get(Pipeline, target_id)
        else:
            result = await db.execute(
                select(Pipeline).where(Pipeline.name == pipeline_ref)
            )
            target = result.scalar_one_or_none()

        if target is None:
            yield LogLine(stream="stderr", content=f"Error: Pipeline '{pipeline_ref}' not found\n")
            yield StepResult(exit_code=1, status="failed", error=f"Pipeline '{pipeline_ref}' not found")
            return

        if not target.enabled:
            yield LogLine(stream="stderr", content=f"Error: Pipeline '{target.name}' is disabled\n")
            yield StepResult(exit_code=1, status="failed", error=f"Pipeline '{target.name}' is disabled")
            return

        max_number = await db.scalar(
            select(func.coalesce(func.max(Build.number), 0)).where(
                Build.pipeline_id == target.id
            )
        )
        next_number = (max_number or 0) + 1

        child_build = Build(
            pipeline_id=target.id,
            number=next_number,
            branch=branch or target.default_branch,
            status="pending",
            triggered_by=None,
            trigger_type="pipeline",
            params_json=params if params else None,
        )
        db.add(child_build)
        await db.flush()

        if target.yaml_content:
            pipeline_def = parse_yaml_pipeline(target.yaml_content)
            stage_defs = compile_to_build_graph(pipeline_def)

            for sort_order, stage_def in enumerate(stage_defs):
                stage = Stage(
                    build_id=child_build.id,
                    name=stage_def["name"],
                    status="pending",
                    sort_order=sort_order,
                )
                db.add(stage)
                await db.flush()

                for step_order, step_def in enumerate(stage_def.get("steps", [])):
                    step_type = step_def.get("step_type", "run")
                    step_config = step_def.get("config", {})
                    command = step_config.get("command") if step_type == "run" else None

                    step = Step(
                        stage_id=stage.id,
                        name=step_def.get("name", f"step-{step_order}"),
                        step_type=step_type,
                        command=command,
                        config_json=step_config if step_config else None,
                        status="pending",
                        sort_order=step_order,
                    )
                    db.add(step)

        await db.commit()
        await db.refresh(child_build)

        yield LogLine(
            stream="stdout",
            content=f"Triggered pipeline '{target.name}' — build #{child_build.number} ({child_build.id})\n",
        )

        run_build.delay(str(child_build.id))

        if not wait:
            yield StepResult(
                exit_code=0,
                status="success",
                outputs={
                    "build_id": str(child_build.id),
                    "build_number": child_build.number,
                    "pipeline_name": target.name,
                },
            )
            return

        yield LogLine(
            stream="system",
            content=f"Waiting for build #{child_build.number} to complete (timeout {timeout}s)…\n",
        )

        elapsed = 0.0
        last_log = 0.0
        while elapsed < timeout:
            await db.refresh(child_build)

            if child_build.status in ("success", "failed", "cancelled"):
                if child_build.status == "success":
                    yield LogLine(stream="stdout", content=f"Build #{child_build.number} completed successfully.\n")
                    yield StepResult(
                        exit_code=0,
                        status="success",
                        outputs={
                            "build_id": str(child_build.id),
                            "build_number": child_build.number,
                            "build_status": child_build.status,
                            "pipeline_name": target.name,
                        },
                    )
                else:
                    yield LogLine(stream="stderr", content=f"Build #{child_build.number} finished with status: {child_build.status}\n")
                    yield StepResult(
                        exit_code=1,
                        status="failed",
                        error=f"Triggered build finished with status: {child_build.status}",
                        outputs={
                            "build_id": str(child_build.id),
                            "build_number": child_build.number,
                            "build_status": child_build.status,
                            "pipeline_name": target.name,
                        },
                    )
                return

            if elapsed - last_log >= 30:
                remaining = int(timeout - elapsed)
                yield LogLine(
                    stream="system",
                    content=f"Build #{child_build.number} is '{child_build.status}'… ({remaining}s remaining)\n",
                )
                last_log = elapsed

            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

        yield LogLine(stream="stderr", content=f"Timed out waiting for build #{child_build.number} after {timeout}s.\n")
        yield StepResult(exit_code=1, status="failed", error="Triggered build timed out")

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not config.get("pipeline"):
            errors.append("'pipeline' is required")
        timeout = config.get("timeout", _DEFAULT_TIMEOUT)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.append("'timeout' must be a positive number")
        return errors


register("trigger_pipeline", TriggerPipelineHandler())
