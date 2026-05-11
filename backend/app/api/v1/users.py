import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import require_permission
from app.core.security import hash_password
from app.database import get_db
from app.models.role import Role, UserRole
from app.models.user import User
from app.schemas.roles import UserRoleAssign, UserRoleResponse
from app.schemas.users import (
    UserCreateRequest,
    UserCreatedResponse,
    UserDetailResponse,
    UserUpdateRequest,
)

router = APIRouter()


def _user_to_detail(user: User) -> dict:
    roles = []
    for ur in user.user_roles:
        roles.append({
            "id": str(ur.id),
            "role_id": str(ur.role_id),
            "role_name": ur.role.name if ur.role else None,
            "scope_type": ur.scope_type,
            "scope_id": str(ur.scope_id) if ur.scope_id else None,
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
    }


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
    return [_user_to_detail(u) for u in result.scalars().all()]


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

    user_role = UserRole(
        user_id=user.id,
        role_id=body.role_id,
        scope_type="global",
    )
    db.add(user_role)
    await db.flush()

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
    return _user_to_detail(user)


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
        if user.id == current_user.id and not body.is_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You cannot remove your own admin privilege",
            )
        user.is_admin = body.is_admin
    await db.commit()
    await db.refresh(user)
    return _user_to_detail(user)


@router.post("/{user_id}/roles", response_model=UserRoleResponse, status_code=status.HTTP_201_CREATED)
async def assign_role(
    user_id: uuid.UUID,
    body: UserRoleAssign,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("users.manage")),
) -> dict:
    user_result = await db.execute(select(User).where(User.id == user_id))
    if user_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    role_result = await db.execute(select(Role).where(Role.id == body.role_id))
    role = role_result.scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

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
    _current_user: User = Depends(require_permission("users.manage")),
) -> None:
    result = await db.execute(
        select(UserRole).where(UserRole.id == user_role_id, UserRole.user_id == user_id)
    )
    user_role = result.scalar_one_or_none()
    if user_role is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User role assignment not found"
        )
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

