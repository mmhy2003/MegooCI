"""Record a pipeline-validation failure onto an already-created build.

Used by the non-interactive trigger paths (git webhook, trigger_pipeline step),
where there is no user to return an HTTP 400 to. We still create the build,
mark it failed, and attach the validation errors as a synthetic
``validation`` / ``yaml-check`` stage so they appear in the build-logs UI with
no schema migration.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import Build, LogChunk, Stage, Step
from app.services.pipeline_compiler import PipelineError


def format_validation_errors(errors: list[PipelineError]) -> str:
    lines: list[str] = []
    for e in errors:
        if e.line is not None and e.column is not None:
            lines.append(f"Line {e.line}, col {e.column}: {e.message}")
        elif e.line is not None:
            lines.append(f"Line {e.line}: {e.message}")
        else:
            lines.append(e.message)
    return "\n".join(lines)


async def record_pipeline_validation_failure(
    db: AsyncSession, build: Build, errors: list[PipelineError]
) -> None:
    now = datetime.now(timezone.utc)
    build.status = "failed"
    build.finished_at = now

    stage = Stage(build_id=build.id, name="validation", status="failed", sort_order=0)
    db.add(stage)
    await db.flush()

    step = Step(
        stage_id=stage.id,
        name="yaml-check",
        step_type="run",
        status="failed",
        exit_code=1,
        sort_order=0,
    )
    db.add(step)
    await db.flush()

    content = "Pipeline validation failed:\n" + format_validation_errors(errors) + "\n"
    db.add(
        LogChunk(step_id=step.id, seq=0, timestamp=now, stream="stderr", content=content)
    )
