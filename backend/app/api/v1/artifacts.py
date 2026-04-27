"""Build artifact endpoints — list, upload, download, delete.

Upload is designed for agents / internal callers that POST multipart files
after a build step completes.  Download streams the stored file back.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import require_permission
from app.database import get_db
from app.models.artifact import Artifact
from app.models.build import Build
from app.models.pipeline import Pipeline
from app.models.user import User
from app.schemas.artifact import ArtifactListItem, ArtifactResponse

router = APIRouter()


# ── Global list ──────────────────────────────────────────────────────────


@router.get("/artifacts", response_model=list[ArtifactListItem])
async def list_all_artifacts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("artifacts.read")),
) -> list[ArtifactListItem]:
    """Return all artifacts across all builds, newest first."""
    result = await db.execute(
        select(
            Artifact.id,
            Artifact.build_id,
            Build.number.label("build_number"),
            Build.pipeline_id,
            Pipeline.name.label("pipeline_name"),
            Artifact.relative_path,
            Artifact.size_bytes,
            Artifact.checksum_sha256,
            Artifact.retention_until,
            Artifact.created_at,
        )
        .join(Build, Artifact.build_id == Build.id)
        .join(Pipeline, Build.pipeline_id == Pipeline.id)
        .order_by(Artifact.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = result.all()
    return [
        ArtifactListItem(
            id=row.id,
            build_id=row.build_id,
            build_number=row.build_number,
            pipeline_id=row.pipeline_id,
            pipeline_name=row.pipeline_name,
            relative_path=row.relative_path,
            size_bytes=row.size_bytes,
            checksum_sha256=row.checksum_sha256,
            retention_until=row.retention_until,
            created_at=row.created_at,
        )
        for row in rows
    ]


# ── Per-build list ───────────────────────────────────────────────────────


@router.get(
    "/builds/{build_id}/artifacts",
    response_model=list[ArtifactResponse],
)
async def list_artifacts(
    build_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("artifacts.read")),
) -> list[Artifact]:
    build = await db.get(Build, build_id)
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )
    result = await db.execute(
        select(Artifact)
        .where(Artifact.build_id == build_id)
        .order_by(Artifact.relative_path)
    )
    return list(result.scalars().all())


# ── Upload ───────────────────────────────────────────────────────────────


@router.post(
    "/builds/{build_id}/artifacts",
    response_model=ArtifactResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_artifact(
    build_id: uuid.UUID,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("artifacts.manage")),
) -> Artifact:
    settings = get_settings()
    build = await db.get(Build, build_id)
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )

    # Read file content and compute checksum.
    content = await file.read()
    sha256 = hashlib.sha256(content).hexdigest()
    size_bytes = len(content)

    # Store under <STORAGE_ROOT>/artifacts/<build_id>/<filename>.
    artifact_dir = Path(settings.MEGOOCI_STORAGE_ROOT) / "artifacts" / str(build_id)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # Sanitise filename — strip directory traversal.
    filename = Path(file.filename or "artifact").name
    storage_path = artifact_dir / filename

    # If a file with the same name already exists for this build, add a
    # short UUID suffix to prevent silent overwrites.
    if storage_path.exists():
        stem = storage_path.stem
        suffix = storage_path.suffix
        storage_path = artifact_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

    storage_path.write_bytes(content)

    retention_until = datetime.now(timezone.utc) + timedelta(
        days=settings.MEGOOCI_ARTIFACT_RETENTION_DAYS,
    )

    artifact = Artifact(
        build_id=build_id,
        relative_path=filename,
        size_bytes=size_bytes,
        checksum_sha256=sha256,
        storage_path=str(storage_path),
        retention_until=retention_until,
    )
    db.add(artifact)
    await db.commit()
    await db.refresh(artifact)
    return artifact


# ── Download ─────────────────────────────────────────────────────────────


@router.get("/artifacts/{artifact_id}/download")
async def download_artifact(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("artifacts.read")),
) -> FileResponse:
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found"
        )

    path = Path(artifact.storage_path)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Artifact file missing from storage",
        )

    return FileResponse(
        path=str(path),
        filename=artifact.relative_path,
        media_type="application/octet-stream",
    )


# ── Delete ───────────────────────────────────────────────────────────────


@router.delete(
    "/artifacts/{artifact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_artifact(
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("artifacts.manage")),
) -> None:
    artifact = await db.get(Artifact, artifact_id)
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found"
        )

    # Remove file from disk (ignore if already gone).
    path = Path(artifact.storage_path)
    if path.is_file():
        path.unlink()

    await db.delete(artifact)
    await db.commit()
