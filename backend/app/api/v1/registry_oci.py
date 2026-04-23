"""OCI Distribution Spec v1.1 endpoints — ``/v2/`` route tree.

Implements the minimal set required for ``docker push`` / ``docker pull``
compatibility:

- ``GET  /v2/``                               — version check
- ``GET  /v2/token``                          — token auth endpoint
- ``GET  /v2/_catalog``                       — repository listing
- ``GET  /v2/<name>/tags/list``               — tag listing
- ``HEAD /v2/<name>/manifests/<ref>``         — manifest existence
- ``GET  /v2/<name>/manifests/<ref>``         — pull manifest
- ``PUT  /v2/<name>/manifests/<ref>``         — push manifest
- ``DELETE /v2/<name>/manifests/<ref>``        — delete manifest
- ``HEAD /v2/<name>/blobs/<digest>``          — blob existence
- ``GET  /v2/<name>/blobs/<digest>``          — pull blob
- ``POST /v2/<name>/blobs/uploads/``          — initiate upload
- ``PATCH /v2/<name>/blobs/uploads/<uuid>``   — upload chunk
- ``PUT  /v2/<name>/blobs/uploads/<uuid>``    — complete upload
- ``DELETE /v2/<name>/blobs/uploads/<uuid>``  — cancel upload
- ``POST /v2/<name>/blobs/uploads/?mount=``   — cross-repo blob mount

Image names follow the format ``<project_slug>/<repo_name>`` (two segments).
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.registry_auth import (
    authenticate_basic,
    decode_registry_token,
    issue_registry_token,
)
from app.database import get_db
from app.models.project import Project
from app.models.registry import (
    ContainerImage,
    ContainerRepository,
    ContainerTag,
    RegistryEvent,
)
from app.services import registry_storage as storage

router = APIRouter()

_OCI_MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}

_TAG_RE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9._-]{0,127}$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _registry_disabled_check() -> None:
    settings = get_settings()
    if not settings.MEGOOCI_REGISTRY_ENABLED:
        raise HTTPException(status_code=404, detail="Registry is disabled")


def _auth_challenge(scope: str | None = None, error: str | None = None) -> dict[str, str]:
    settings = get_settings()
    realm = f"{settings.MEGOOCI_PUBLIC_URL}/v2/token"
    parts = [f'Bearer realm="{realm}"', f'service="{settings.MEGOOCI_REGISTRY_HOST}"']
    if scope:
        parts.append(f'scope="{scope}"')
    if error:
        parts.append(f'error="{error}"')
    return {
        "Www-Authenticate": ",".join(parts),
        "Docker-Distribution-Api-Version": "registry/2.0",
    }


def _parse_bearer(authorization: str | None) -> dict | None:
    if not authorization:
        return None
    if authorization.lower().startswith("bearer "):
        return decode_registry_token(authorization[7:])
    return None


def _require_action(token_payload: dict | None, action: str) -> None:
    if token_payload is None:
        raise HTTPException(status_code=401, headers=_auth_challenge())
    if action not in (token_payload.get("access") or []):
        scope = token_payload.get("scope", "")
        raise HTTPException(
            status_code=401,
            headers=_auth_challenge(scope=scope, error="insufficient_scope"),
        )


async def _get_or_create_repo(
    db: AsyncSession, project: Project, repo_name: str
) -> ContainerRepository:
    result = await db.execute(
        select(ContainerRepository).where(
            ContainerRepository.project_id == project.id,
            ContainerRepository.name == repo_name,
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        repo = ContainerRepository(
            project_id=project.id,
            name=repo_name,
        )
        db.add(repo)
        await db.flush()
    return repo


async def _resolve_project(db: AsyncSession, project_slug: str) -> Project:
    """Resolve a project by slug — raises 404 for read-only operations."""
    result = await db.execute(
        select(Project).where(Project.slug == project_slug)
    )
    project = result.scalar_one_or_none()
    if project is None:
        settings = get_settings()
        host = settings.MEGOOCI_REGISTRY_HOST
        raise HTTPException(
            status_code=404,
            detail={
                "errors": [{
                    "code": "NAME_UNKNOWN",
                    "message": (
                        f"Project '{project_slug}' does not exist. "
                        f"Create it in the MegooCI UI first, then push: "
                        f"docker push {host}/{project_slug}/<repo>:<tag>"
                    ),
                }]
            },
        )
    return project




async def _check_anonymous_pull(
    db: AsyncSession, project_slug: str, repo_name: str
) -> bool:
    result = await db.execute(
        select(ContainerRepository)
        .join(Project, ContainerRepository.project_id == Project.id)
        .where(Project.slug == project_slug, ContainerRepository.name == repo_name)
    )
    repo = result.scalar_one_or_none()
    return repo.allow_anonymous_pull if repo else False


async def _record_event(
    db: AsyncSession,
    repo: ContainerRepository,
    event_type: str,
    digest: str | None = None,
    tag: str | None = None,
    actor_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> None:
    event = RegistryEvent(
        repository_id=repo.id,
        event_type=event_type,
        digest=digest,
        tag=tag,
        actor_id=actor_id,
        ip_address=request.client.host if request and request.client else None,
        user_agent=request.headers.get("user-agent") if request else None,
    )
    db.add(event)


# ---------------------------------------------------------------------------
# Token endpoint (Docker token auth)
# ---------------------------------------------------------------------------

@router.get("/v2/token")
@router.post("/v2/token")
async def get_token(
    request: Request,
    service: str = Query(""),
    scope: str = Query(""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _registry_disabled_check()

    svc = service
    scp = scope
    form_username = ""
    form_password = ""
    if request.method == "POST":
        try:
            form = await request.form()
            svc = svc or str(form.get("service", ""))
            scp = scp or str(form.get("scope", ""))
            form_username = str(form.get("username", ""))
            form_password = str(form.get("password", ""))
        except Exception:
            pass

    auth_header = request.headers.get("authorization", "")
    subject: str | None = None
    actions: list[str] = []
    actor_id: uuid.UUID | None = None

    if auth_header.lower().startswith("basic "):
        decoded = base64.b64decode(auth_header[6:]).decode("utf-8", errors="replace")
        username, _, password = decoded.partition(":")
        subject, actor_id, actions = await authenticate_basic(db, username, password)
        if subject is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    elif form_username and form_password:
        subject, actor_id, actions = await authenticate_basic(
            db, form_username, form_password
        )
        if subject is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    else:
        actions = ["pull"]
        subject = "anonymous"

    token = issue_registry_token(subject, actions, scope=scp or None)
    return {
        "token": token,
        "access_token": token,
        "expires_in": 1800,
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# /v2/ — version check
# ---------------------------------------------------------------------------

@router.get("/v2/")
@router.head("/v2/")
async def v2_check() -> Response:
    _registry_disabled_check()
    return Response(
        status_code=200,
        headers={
            "Docker-Distribution-Api-Version": "registry/2.0",
        },
    )


# ---------------------------------------------------------------------------
# /v2/_catalog — list repositories
# ---------------------------------------------------------------------------

@router.get("/v2/_catalog")
async def catalog(
    request: Request,
    n: int = Query(100, ge=1, le=1000),
    last: str = Query(""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))
    _require_action(token_payload, "pull")

    q = (
        select(
            Project.slug,
            ContainerRepository.name,
        )
        .join(Project, ContainerRepository.project_id == Project.id)
        .order_by(Project.slug, ContainerRepository.name)
        .limit(n)
    )
    if last:
        parts = last.split("/", 1)
        if len(parts) == 2:
            q = q.where(
                (Project.slug > parts[0])
                | ((Project.slug == parts[0]) & (ContainerRepository.name > parts[1]))
            )

    result = await db.execute(q)
    repos = [f"{row[0]}/{row[1]}" for row in result.all()]
    return {"repositories": repos}


# ---------------------------------------------------------------------------
# /v2/<project_slug>/<repo_name>/tags/list
# ---------------------------------------------------------------------------

@router.get("/v2/{project_slug}/{repo_name}/tags/list")
async def list_tags(
    project_slug: str,
    repo_name: str,
    request: Request,
    n: int = Query(100, ge=1, le=10000),
    last: str = Query(""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))

    anon_ok = await _check_anonymous_pull(db, project_slug, repo_name)
    if not anon_ok:
        _require_action(token_payload, "pull")

    project = await _resolve_project(db, project_slug)
    repo = (await db.execute(
        select(ContainerRepository).where(
            ContainerRepository.project_id == project.id,
            ContainerRepository.name == repo_name,
        )
    )).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    q = (
        select(ContainerTag.name)
        .where(ContainerTag.repository_id == repo.id)
        .order_by(ContainerTag.name)
        .limit(n)
    )
    if last:
        q = q.where(ContainerTag.name > last)

    result = await db.execute(q)
    tags = [row[0] for row in result.all()]
    return {"name": f"{project_slug}/{repo_name}", "tags": tags}


# ---------------------------------------------------------------------------
# Manifests — HEAD / GET / PUT / DELETE
# ---------------------------------------------------------------------------

@router.head("/v2/{project_slug}/{repo_name}/manifests/{reference}")
@router.get("/v2/{project_slug}/{repo_name}/manifests/{reference}")
async def get_manifest(
    project_slug: str,
    repo_name: str,
    reference: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))

    anon_ok = await _check_anonymous_pull(db, project_slug, repo_name)
    if not anon_ok:
        _require_action(token_payload, "pull")

    repo_path = f"{project_slug}/{repo_name}"
    data = storage.read_manifest(repo_path, reference)
    if data is None:
        raise HTTPException(status_code=404, detail="Manifest not found")

    digest = storage.manifest_digest(data)
    try:
        manifest_json = json.loads(data)
        media_type = manifest_json.get("mediaType", "application/vnd.oci.image.manifest.v1+json")
    except Exception:
        media_type = "application/vnd.oci.image.manifest.v1+json"

    if request.method == "HEAD":
        return Response(
            status_code=200,
            headers={
                "Docker-Content-Digest": digest,
                "Content-Type": media_type,
                "Content-Length": str(len(data)),
                "Docker-Distribution-Api-Version": "registry/2.0",
            },
        )

    return Response(
        content=data,
        status_code=200,
        media_type=media_type,
        headers={
            "Docker-Content-Digest": digest,
            "Docker-Distribution-Api-Version": "registry/2.0",
        },
    )


@router.put("/v2/{project_slug}/{repo_name}/manifests/{reference}")
async def put_manifest(
    project_slug: str,
    repo_name: str,
    reference: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))
    _require_action(token_payload, "push")

    body = await request.body()
    if len(body) > get_settings().MEGOOCI_REGISTRY_MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Manifest too large")

    digest = storage.manifest_digest(body)

    try:
        manifest_json = json.loads(body)
        media_type = manifest_json.get("mediaType", request.headers.get("content-type", ""))
    except Exception:
        media_type = request.headers.get("content-type", "application/vnd.oci.image.manifest.v1+json")

    project = await _resolve_project(db, project_slug)
    repo = await _get_or_create_repo(db, project, repo_name)

    if repo.quota_bytes and (repo.used_bytes + len(body)) > repo.quota_bytes:
        raise HTTPException(status_code=413, detail="Repository quota exceeded")

    if repo.immutable_tags and _TAG_RE.match(reference):
        existing_tag = (await db.execute(
            select(ContainerTag).where(
                ContainerTag.repository_id == repo.id,
                ContainerTag.name == reference,
            )
        )).scalar_one_or_none()
        if existing_tag is not None:
            raise HTTPException(status_code=409, detail=f"Tag '{reference}' is immutable and already exists")

    repo_path = f"{project_slug}/{repo_name}"
    storage.store_manifest(repo_path, reference, body, digest)

    config_digest = manifest_json.get("config", {}).get("digest") if isinstance(manifest_json, dict) else None
    total_size = len(body)
    if isinstance(manifest_json, dict):
        for layer in manifest_json.get("layers", []):
            total_size += layer.get("size", 0)

    existing_image = (await db.execute(
        select(ContainerImage).where(
            ContainerImage.repository_id == repo.id,
            ContainerImage.digest == digest,
        )
    )).scalar_one_or_none()

    actor_id = None
    sub = token_payload.get("sub", "") if token_payload else ""
    try:
        actor_id = uuid.UUID(sub)
    except (ValueError, AttributeError):
        pass

    if existing_image is None:
        image = ContainerImage(
            repository_id=repo.id,
            digest=digest,
            media_type=media_type,
            size_bytes=total_size,
            config_digest=config_digest,
            pushed_by=actor_id,
        )
        db.add(image)
        await db.flush()
        repo.used_bytes = (repo.used_bytes or 0) + total_size
    else:
        image = existing_image

    if _TAG_RE.match(reference):
        existing_tag = (await db.execute(
            select(ContainerTag).where(
                ContainerTag.repository_id == repo.id,
                ContainerTag.name == reference,
            )
        )).scalar_one_or_none()
        if existing_tag:
            existing_tag.image_id = image.id
            existing_tag.updated_at = datetime.now(timezone.utc)
        else:
            tag = ContainerTag(
                repository_id=repo.id,
                image_id=image.id,
                name=reference,
            )
            db.add(tag)

    await _record_event(
        db, repo, "image.pushed",
        digest=digest,
        tag=reference if _TAG_RE.match(reference) else None,
        actor_id=actor_id,
        request=request,
    )

    return Response(
        status_code=201,
        headers={
            "Docker-Content-Digest": digest,
            "Location": f"/v2/{repo_path}/manifests/{digest}",
            "Docker-Distribution-Api-Version": "registry/2.0",
        },
    )


@router.delete("/v2/{project_slug}/{repo_name}/manifests/{reference}")
async def delete_manifest(
    project_slug: str,
    repo_name: str,
    reference: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))
    _require_action(token_payload, "delete")

    project = await _resolve_project(db, project_slug)
    repo = (await db.execute(
        select(ContainerRepository).where(
            ContainerRepository.project_id == project.id,
            ContainerRepository.name == repo_name,
        )
    )).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404)

    if reference.startswith("sha256:"):
        image = (await db.execute(
            select(ContainerImage).where(
                ContainerImage.repository_id == repo.id,
                ContainerImage.digest == reference,
            )
        )).scalar_one_or_none()
    else:
        tag = (await db.execute(
            select(ContainerTag).where(
                ContainerTag.repository_id == repo.id,
                ContainerTag.name == reference,
            )
        )).scalar_one_or_none()
        if tag is None:
            raise HTTPException(status_code=404)
        image = (await db.execute(
            select(ContainerImage).where(ContainerImage.id == tag.image_id)
        )).scalar_one_or_none()
        await db.delete(tag)

    if image:
        repo_path = f"{project_slug}/{repo_name}"
        storage.delete_manifest(repo_path, image.digest)
        repo.used_bytes = max(0, (repo.used_bytes or 0) - image.size_bytes)
        await db.delete(image)

    actor_id = None
    sub = token_payload.get("sub", "") if token_payload else ""
    try:
        actor_id = uuid.UUID(sub)
    except (ValueError, AttributeError):
        pass

    await _record_event(db, repo, "image.deleted", digest=reference, actor_id=actor_id, request=request)

    return Response(status_code=202)


# ---------------------------------------------------------------------------
# Blobs — HEAD / GET
# ---------------------------------------------------------------------------

@router.head("/v2/{project_slug}/{repo_name}/blobs/{digest}")
@router.get("/v2/{project_slug}/{repo_name}/blobs/{digest}")
async def get_blob(
    project_slug: str,
    repo_name: str,
    digest: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))

    anon_ok = await _check_anonymous_pull(db, project_slug, repo_name)
    if not anon_ok:
        _require_action(token_payload, "pull")

    if not storage.blob_exists(digest):
        raise HTTPException(status_code=404, detail="Blob not found")

    size = storage.blob_size(digest)

    if request.method == "HEAD":
        return Response(
            status_code=200,
            headers={
                "Docker-Content-Digest": digest,
                "Content-Length": str(size),
                "Content-Type": "application/octet-stream",
                "Docker-Distribution-Api-Version": "registry/2.0",
            },
        )

    def _stream():
        p = storage.blob_path(digest)
        with open(p, "rb") as f:
            while chunk := f.read(1 << 20):
                yield chunk

    return StreamingResponse(
        _stream(),
        status_code=200,
        media_type="application/octet-stream",
        headers={
            "Docker-Content-Digest": digest,
            "Content-Length": str(size),
            "Docker-Distribution-Api-Version": "registry/2.0",
        },
    )


# ---------------------------------------------------------------------------
# Blob uploads — POST (initiate) / PATCH (chunk) / PUT (complete) / DELETE
# ---------------------------------------------------------------------------

@router.post("/v2/{project_slug}/{repo_name}/blobs/uploads")
@router.post("/v2/{project_slug}/{repo_name}/blobs/uploads/")
async def start_upload(
    project_slug: str,
    repo_name: str,
    request: Request,
    mount: str = Query(""),
    from_repo: str = Query("", alias="from"),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))
    _require_action(token_payload, "push")

    project = await _resolve_project(db, project_slug)
    await _get_or_create_repo(db, project, repo_name)

    if mount and storage.blob_exists(mount):
        return Response(
            status_code=201,
            headers={
                "Docker-Content-Digest": mount,
                "Location": f"/v2/{project_slug}/{repo_name}/blobs/{mount}",
                "Docker-Distribution-Api-Version": "registry/2.0",
            },
        )

    upload_id = storage.create_upload()
    return Response(
        status_code=202,
        headers={
            "Location": f"/v2/{project_slug}/{repo_name}/blobs/uploads/{upload_id}",
            "Docker-Upload-UUID": upload_id,
            "Range": "0-0",
            "Docker-Distribution-Api-Version": "registry/2.0",
        },
    )


@router.patch("/v2/{project_slug}/{repo_name}/blobs/uploads/{upload_id}")
async def upload_chunk(
    project_slug: str,
    repo_name: str,
    upload_id: str,
    request: Request,
) -> Response:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))
    _require_action(token_payload, "push")

    if not storage.upload_exists(upload_id):
        raise HTTPException(status_code=404, detail="Upload not found")

    body = await request.body()
    max_bytes = get_settings().MEGOOCI_REGISTRY_MAX_UPLOAD_MB * 1024 * 1024
    current = storage.upload_offset(upload_id)
    if current + len(body) > max_bytes:
        storage.cancel_upload(upload_id)
        raise HTTPException(status_code=413, detail="Layer too large")

    new_offset = storage.append_upload(upload_id, body)

    return Response(
        status_code=202,
        headers={
            "Location": f"/v2/{project_slug}/{repo_name}/blobs/uploads/{upload_id}",
            "Docker-Upload-UUID": upload_id,
            "Range": f"0-{new_offset - 1}",
            "Docker-Distribution-Api-Version": "registry/2.0",
        },
    )


@router.put("/v2/{project_slug}/{repo_name}/blobs/uploads/{upload_id}")
async def complete_upload(
    project_slug: str,
    repo_name: str,
    upload_id: str,
    request: Request,
    digest: str = Query(...),
) -> Response:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))
    _require_action(token_payload, "push")

    if not storage.upload_exists(upload_id):
        raise HTTPException(status_code=404, detail="Upload not found")

    trailing = await request.body()
    if trailing:
        storage.append_upload(upload_id, trailing)

    if not storage.finalize_upload(upload_id, digest):
        raise HTTPException(status_code=400, detail="Digest verification failed")

    return Response(
        status_code=201,
        headers={
            "Docker-Content-Digest": digest,
            "Location": f"/v2/{project_slug}/{repo_name}/blobs/{digest}",
            "Docker-Distribution-Api-Version": "registry/2.0",
        },
    )


@router.delete("/v2/{project_slug}/{repo_name}/blobs/uploads/{upload_id}")
async def cancel_upload_endpoint(
    project_slug: str,
    repo_name: str,
    upload_id: str,
    request: Request,
) -> Response:
    _registry_disabled_check()
    token_payload = _parse_bearer(request.headers.get("authorization"))
    _require_action(token_payload, "push")

    storage.cancel_upload(upload_id)
    return Response(status_code=204)


