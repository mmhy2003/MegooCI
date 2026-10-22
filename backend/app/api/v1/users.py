import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.audit import record as audit_record
from app.core.deps import require_permission
from app.core.security import hash_password
from app.database import get_db
from app.models.role import Role, UserRole
from app.models.user import User
from app.schemas.roles import UserRoleAssign, UserRoleResponse
from app.services.administrators import (
    AdministratorChangeError,
    is_admin_role_row,
    set_administrator,
)
from app.schemas.users import (
    UserCreateRequest,
    UserCreatedResponse,
    UserDetailResponse,
    UserUpdateRequest,
)
from app.services.two_step import status as two_step_status
from app.services.two_step.reset import reset as reset_two_step

router = APIRouter()


def _user_to_detail(
    user: User, project_names: dict | None = None, two_step_enabled: bool = False
) -> dict:
    if project_names is None:
        project_names = {}
    roles = []
    for ur in user.user_roles:
        roles.append({
            "id": str(ur.id),
            "role_id": str(ur.role_id),
            "role_name": ur.role.name if ur.role else None,
            "scope_type": ur.scope_type,
            "scope_id": str(ur.scope_id) if ur.scope_id else None,
            "project_name": project_names.get(ur.scope_id) if ur.scope_type == "project" else None,
        })
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
        "auth_provider": user.auth_provider,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "roles": roles,
        "two_step_enabled": two_step_enabled,
    }


async def _project_name_map(db: AsyncSession, users: list) -> dict:
    from app.models.project import Project
    pids = {
        ur.scope_id
        for u in users
        for ur in u.user_roles
        if ur.scope_type == "project" and ur.scope_id
    }
    if not pids:
        return {}
    rows = await db.execute(select(Project.id, Project.name).where(Project.id.in_(pids)))
    return {pid: name for pid, name in rows.all()}


@router.get("/", response_model=list[UserDetailResponse])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("users.manage")),
) -> list[dict]:
    result = await db.execute(
        select(User)
        .options(selectinload(User.user_roles).selectinload(UserRole.role))
        .order_by(User.created_at)
        .offset(skip)
        .limit(limit)
    )
    users = list(result.scalars().all())
    pnames = await _project_name_map(db, users)
    with_two_step = await two_step_status.enabled_user_ids(db)
    return [_user_to_detail(u, pnames, u.id in with_two_step) for u in users]


@router.post("/", response_model=UserCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreateRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("users.manage")),
) -> dict:
    existing = await db.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    role_result = await db.execute(select(Role).where(Role.id == body.role_id))
    role = role_result.scalar_one_or_none()
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Role not found"
        )

    generated_password = secrets.token_urlsafe(16)

    user = User(
        email=body.email,
        name=body.name,
        hashed_password=hash_password(generated_password),
        is_admin=role.name == "admin",
        is_active=True,
    )
    db.add(user)
    await db.flush()

    if role.name == "admin":
        db.add(UserRole(user_id=user.id, role_id=body.role_id, scope_type="global"))
        await db.flush()
    # Non-admins start with no role; an admin assigns project roles afterward.

    result = await db.execute(
        select(User)
        .options(selectinload(User.user_roles).selectinload(UserRole.role))
        .where(User.id == user.id)
    )
    user = result.scalar_one()

    detail = _user_to_detail(user)
    detail["generated_password"] = generated_password
    return detail


@router.get("/{user_id}", response_model=UserDetailResponse)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("users.manage")),
) -> dict:
    result = await db.execute(
        select(User)
        .options(selectinload(User.user_roles).selectinload(UserRole.role))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    pnames = await _project_name_map(db, [user])
    return _user_to_detail(user, pnames, await two_step_status.is_on(db, user.id))


@router.put("/{user_id}", response_model=UserDetailResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("users.manage")),
) -> dict:
    result = await db.execute(
        select(User)
        .options(selectinload(User.user_roles).selectinload(UserRole.role))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    if user.id == current_user.id and body.is_active is False:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot deactivate your own account",
        )

    if body.name is not None:
        user.name = body.name
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_admin is not None:
        await _set_administrator(db, user, body.is_admin, current_user)
    await db.commit()
    await db.refresh(user)
    pnames = await _project_name_map(db, [user])
    return _user_to_detail(user, pnames, await two_step_status.is_on(db, user.id))


async def _holds_global_role(db: AsyncSession, user_id: uuid.UUID, role_id: uuid.UUID) -> bool:
    found = await db.execute(
        select(UserRole.id).where(
            UserRole.user_id == user_id,
            UserRole.role_id == role_id,
            UserRole.scope_type == "global",
        )
    )
    return found.first() is not None


async def _set_administrator(
    db: AsyncSession, user: User, on: bool, current_user: User | None
) -> None:
    try:
        await set_administrator(
            db, user, on, actor_id=current_user.id if current_user else None
        )
    except AdministratorChangeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/{user_id}/two-step/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_two_step(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("users.manage")),
) -> None:
    """Remove a user's authenticator app, passkeys and recovery codes, for a
    user who has lost all of them. Their open sessions are left alone. If
    two-step verification is required they set it up again at the next
    sign-in."""
    if user_id == current_user.id:
        # Removing one's own methods asks for the password, in Settings.
        # This route must not be a way around that.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Change your own two-step verification in Settings.",
        )
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    was_on = await reset_two_step(db, user_id)
    await audit_record(
        action="two_step_reset_by_admin",
        actor_id=current_user.id,
        target_type="user",
        target_id=user_id,
        metadata={"was_on": was_on},
        db=db,
    )


@router.post("/{user_id}/roles", response_model=UserRoleResponse, status_code=status.HTTP_201_CREATED)
async def assign_role(
    user_id: uuid.UUID,
    body: UserRoleAssign,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("users.manage")),
) -> dict:
    from app.models.project import Project

    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role_result = await db.execute(select(Role).where(Role.id == body.role_id))
    role = role_result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    # Project-scoped assignment validation.
    if body.scope_type == "project":
        if body.scope_id is None or await db.get(Project, body.scope_id) is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="scope_id must reference an existing project",
            )
        if role.name == "admin":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The admin role cannot be scoped to a project",
            )
        # One role per (user, project): replace any existing project-scoped row.
        existing_proj = await db.execute(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.scope_type == "project",
                UserRole.scope_id == body.scope_id,
            )
        )
        prior = existing_proj.scalar_one_or_none()
        if prior is not None:
            prior.role_id = body.role_id
            await db.flush()
            await db.refresh(prior)
            return {
                "id": prior.id,
                "user_id": prior.user_id,
                "role_id": prior.role_id,
                "scope_type": prior.scope_type,
                "scope_id": prior.scope_id,
                "role_name": role.name,
                "created_at": prior.created_at,
            }

    if role.name == "admin" and body.scope_type == "global":
        # The admin role is half of being an administrator; the flag is the
        # other half, so it goes through the one function that sets both.
        if user.is_admin and await _holds_global_role(db, user_id, body.role_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="User already has this role in the specified scope",
            )
        await _set_administrator(db, user, True, current_user)
        row = (await db.execute(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id == body.role_id,
                UserRole.scope_type == "global",
            )
        )).scalar_one()
        return {
            "id": row.id,
            "user_id": row.user_id,
            "role_id": row.role_id,
            "scope_type": row.scope_type,
            "scope_id": row.scope_id,
            "role_name": role.name,
            "created_at": row.created_at,
        }

    # Exact-duplicate guard (unchanged) for non-project or first-time project rows.
    existing = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id,
            UserRole.role_id == body.role_id,
            UserRole.scope_type == body.scope_type,
            UserRole.scope_id == body.scope_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already has this role in the specified scope",
        )

    user_role = UserRole(
        user_id=user_id,
        role_id=body.role_id,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )
    db.add(user_role)
    await db.flush()
    await db.refresh(user_role)
    return {
        "id": user_role.id,
        "user_id": user_role.user_id,
        "role_id": user_role.role_id,
        "scope_type": user_role.scope_type,
        "scope_id": user_role.scope_id,
        "role_name": role.name,
        "created_at": user_role.created_at,
    }


@router.delete("/{user_id}/roles/{user_role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_role(
    user_id: uuid.UUID,
    user_role_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("users.manage")),
) -> None:
    result = await db.execute(
        select(UserRole).where(UserRole.id == user_role_id, UserRole.user_id == user_id)
    )
    user_role = result.scalar_one_or_none()
    if user_role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User role assignment not found"
        )
    if await is_admin_role_row(db, user_role):
        await _set_administrator(db, await db.get(User, user_id), False, current_user)
        return
    await db.delete(user_role)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("users.manage")),
) -> None:
    """Permanently delete a user and all associated role assignments."""
    if user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account",
        )

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    # Remove all role assignments first.
    await db.execute(
        select(UserRole).where(UserRole.user_id == user_id)
    )
    role_results = await db.execute(
        select(UserRole).where(UserRole.user_id == user_id)
    )
    for ur in role_results.scalars().all():
        await db.delete(ur)

    await db.delete(user)

