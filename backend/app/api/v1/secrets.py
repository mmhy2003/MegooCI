import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.access import accessible_project_ids, ALL_PROJECTS, has_global_permission
from app.core.deps import check_scoped_permission, get_current_active_user
from app.core.security import encrypt_secret
from app.database import get_db
from app.models.secret import EnvVar, Secret
from app.models.user import User
from app.schemas.secret import (
    EnvVarCreate,
    EnvVarResponse,
    EnvVarUpdate,
    SecretCreate,
    SecretResponse,
    SecretUpdate,
)
from app.services.pipeline_ref_renamer import rename_pipeline_references

router = APIRouter()

# ── Secrets ──────────────────────────────────────────────────────────────


@router.get("/secrets", response_model=list[SecretResponse])
async def list_secrets(
    scope_type: str = Query(...),
    scope_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Secret]:
    if scope_type == "project" and scope_id is not None:
        pids = accessible_project_ids(_current_user, "secrets.read")
        if pids is not ALL_PROJECTS and scope_id not in pids:
            return []
    else:
        # Global (or unrecognised) scope: require a global secrets.read grant.
        if not has_global_permission(_current_user, "secrets.read"):
            return []
    query = select(Secret).where(Secret.scope_type == scope_type)
    if scope_id is not None:
        query = query.where(Secret.scope_id == scope_id)
    else:
        query = query.where(Secret.scope_id.is_(None))
    result = await db.execute(query.order_by(Secret.name))
    return list(result.scalars().all())


@router.post("/secrets", response_model=SecretResponse, status_code=status.HTTP_201_CREATED)
async def create_secret(
    body: SecretCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Secret:
    if body.scope_type == "project" and body.scope_id is not None:
        check_scoped_permission(current_user, "secrets.manage", "project", body.scope_id)
    else:
        if not has_global_permission(current_user, "secrets.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission 'secrets.manage' required for global scope",
            )
    settings = get_settings()
    encrypted = encrypt_secret(body.value, settings.MEGOOCI_SECRET_KEY)

    secret = Secret(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        name=body.name,
        secret_type=body.secret_type,
        encrypted_payload=encrypted,
        created_by=current_user.id,
    )
    db.add(secret)
    await db.commit()
    await db.refresh(secret)
    return secret


@router.put("/secrets/{secret_id}", response_model=SecretResponse)
async def update_secret(
    secret_id: uuid.UUID,
    body: SecretUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> JSONResponse:
    settings = get_settings()
    secret = await db.get(Secret, secret_id)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )
    if secret.scope_type == "project" and secret.scope_id is not None:
        check_scoped_permission(_current_user, "secrets.manage", "project", secret.scope_id)
    else:
        if not has_global_permission(_current_user, "secrets.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission 'secrets.manage' required for global scope",
            )

    old_name = secret.name
    updated_pipelines: list[dict] = []

    if body.name is not None:
        secret.name = body.name
    if body.value is not None:
        secret.encrypted_payload = encrypt_secret(body.value, settings.MEGOOCI_SECRET_KEY)
    if body.scope_type is not None:
        secret.scope_type = body.scope_type
        secret.scope_id = body.scope_id  # None for global

    # If name changed, rename references in pipeline YAML.
    new_name = secret.name
    if old_name != new_name:
        updated_pipelines = await rename_pipeline_references(
            db,
            namespace="secrets",
            old_name=old_name,
            new_name=new_name,
            scope_type=secret.scope_type,
            scope_id=secret.scope_id,
        )

    await db.commit()
    await db.refresh(secret)

    response_data = SecretResponse.model_validate(secret).model_dump(mode="json")
    response = JSONResponse(content=response_data)
    if updated_pipelines:
        response.headers["X-Updated-Pipelines"] = json.dumps(updated_pipelines)
    return response


@router.delete("/secrets/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret(
    secret_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> None:
    secret = await db.get(Secret, secret_id)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )
    if secret.scope_type == "project" and secret.scope_id is not None:
        check_scoped_permission(_current_user, "secrets.manage", "project", secret.scope_id)
    else:
        if not has_global_permission(_current_user, "secrets.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission 'secrets.manage' required for global scope",
            )
    await db.delete(secret)
    await db.commit()


# ── Environment Variables ────────────────────────────────────────────────


@router.get("/env-vars", response_model=list[EnvVarResponse])
async def list_env_vars(
    scope_type: str = Query(...),
    scope_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[EnvVar]:
    if scope_type == "project" and scope_id is not None:
        pids = accessible_project_ids(_current_user, "secrets.read")
        if pids is not ALL_PROJECTS and scope_id not in pids:
            return []
    else:
        # Global (or unrecognised) scope: require a global secrets.read grant.
        if not has_global_permission(_current_user, "secrets.read"):
            return []
    query = select(EnvVar).where(EnvVar.scope_type == scope_type)
    if scope_id is not None:
        query = query.where(EnvVar.scope_id == scope_id)
    else:
        query = query.where(EnvVar.scope_id.is_(None))
    result = await db.execute(query.order_by(EnvVar.name))
    return list(result.scalars().all())


@router.post("/env-vars", response_model=EnvVarResponse, status_code=status.HTTP_201_CREATED)
async def create_env_var(
    body: EnvVarCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> EnvVar:
    if body.scope_type == "project" and body.scope_id is not None:
        check_scoped_permission(current_user, "secrets.manage", "project", body.scope_id)
    else:
        if not has_global_permission(current_user, "secrets.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission 'secrets.manage' required for global scope",
            )
    env_var = EnvVar(
        scope_type=body.scope_type,
        scope_id=body.scope_id,
        name=body.name,
        value=body.value,
        created_by=current_user.id,
    )
    db.add(env_var)
    await db.commit()
    await db.refresh(env_var)
    return env_var


@router.put("/env-vars/{env_var_id}", response_model=EnvVarResponse)
async def update_env_var(
    env_var_id: uuid.UUID,
    body: EnvVarUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> JSONResponse:
    env_var = await db.get(EnvVar, env_var_id)
    if env_var is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Environment variable not found"
        )
    if env_var.scope_type == "project" and env_var.scope_id is not None:
        check_scoped_permission(_current_user, "secrets.manage", "project", env_var.scope_id)
    else:
        if not has_global_permission(_current_user, "secrets.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission 'secrets.manage' required for global scope",
            )

    old_name = env_var.name
    updated_pipelines: list[dict] = []

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(env_var, field, value)

    # If name changed, rename references in pipeline YAML.
    new_name = env_var.name
    if old_name != new_name:
        updated_pipelines = await rename_pipeline_references(
            db,
            namespace="env",
            old_name=old_name,
            new_name=new_name,
            scope_type=env_var.scope_type,
            scope_id=env_var.scope_id,
        )

    await db.commit()
    await db.refresh(env_var)

    response_data = EnvVarResponse.model_validate(env_var).model_dump(mode="json")
    response = JSONResponse(content=response_data)
    if updated_pipelines:
        response.headers["X-Updated-Pipelines"] = json.dumps(updated_pipelines)
    return response


@router.delete("/env-vars/{env_var_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_env_var(
    env_var_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> None:
    env_var = await db.get(EnvVar, env_var_id)
    if env_var is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Environment variable not found",
        )
    if env_var.scope_type == "project" and env_var.scope_id is not None:
        check_scoped_permission(_current_user, "secrets.manage", "project", env_var.scope_id)
    else:
        if not has_global_permission(_current_user, "secrets.manage"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission 'secrets.manage' required for global scope",
            )
    await db.delete(env_var)
    await db.commit()
