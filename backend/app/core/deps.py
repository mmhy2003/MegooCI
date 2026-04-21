import uuid
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import decode_token
from app.database import get_db
from app.models.role import UserRole
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
    except ValueError:
        raise credentials_exception

    if payload.get("type") != "access":
        raise credentials_exception

    user_id_str: str | None = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception

    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        raise credentials_exception

    result = await db.execute(
        select(User)
        .options(selectinload(User.user_roles).selectinload(UserRole.role))
        .where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user"
        )
    return current_user


async def get_current_admin_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return current_user


def _collect_permissions(user: User) -> set[str]:
    """Gather all permissions from the user's assigned roles."""
    perms: set[str] = set()
    if user.is_admin:
        perms.add("admin")
    for ur in user.user_roles:
        if ur.role and ur.role.permissions:
            perms.update(ur.role.permissions)
    return perms


def require_permission(permission: str) -> Callable:
    """FastAPI dependency factory that checks for a specific permission."""

    async def _check(
        current_user: User = Depends(get_current_active_user),
    ) -> User:
        if current_user.is_admin:
            return current_user
        perms = _collect_permissions(current_user)
        if permission not in perms and "admin" not in perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required",
            )
        return current_user

    return _check


def get_user_primary_role_name(user: User) -> str | None:
    """Return the name of the user's first global role, or None."""
    if user.is_admin:
        return "admin"
    for ur in user.user_roles:
        if ur.scope_type == "global" and ur.role:
            return ur.role.name
    return None
