import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import decode_token, hash_pat, is_pat
from app.core.token_scopes import ALL_PERMISSIONS, expand_scopes
from app.database import get_db
from app.models.api_token import ApiToken
from app.models.role import UserRole
from app.models.user import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


# ---------------------------------------------------------------------------
# Scope helpers
# ---------------------------------------------------------------------------

def _collect_scoped_permissions(
    user: User,
    scope_type: str = "global",
    scope_id: uuid.UUID | None = None,
) -> set[str]:
    """Gather permissions from roles matching the given scope **plus** all
    global roles (global permissions always apply)."""
    perms: set[str] = set()
    if user.is_admin:
        perms.add("admin")
    for ur in user.user_roles:
        if ur.role and ur.role.permissions:
            if ur.scope_type == "global":
                perms.update(ur.role.permissions)
            elif ur.scope_type == scope_type and ur.scope_id == scope_id:
                perms.update(ur.role.permissions)
    return perms


def _all_role_permissions(user: User) -> set[str]:
    """Union of permissions across all of the user's role assignments."""
    perms: set[str] = set()
    for ur in user.user_roles:
        if ur.role and ur.role.permissions:
            perms.update(ur.role.permissions)
    return perms


def _scoped_role_permissions(
    user: User, scope_type: str, scope_id: uuid.UUID | None
) -> set[str]:
    """Role permissions for a resource scope: global roles always apply, plus
    roles assigned to the matching scope."""
    perms: set[str] = set()
    for ur in user.user_roles:
        if ur.role and ur.role.permissions:
            if ur.scope_type == "global" or (
                ur.scope_type == scope_type and ur.scope_id == scope_id
            ):
                perms.update(ur.role.permissions)
    return perms


def _apply_token_scope(role_perms: set[str], is_admin: bool, scopes: list[str] | None) -> set[str]:
    """Cap a role-permission set by the active PAT scope.

    - scopes is None  -> Full access / JWT session: role perms (+ "admin" if admin).
    - scopes is a list -> scope perms ∩ ceiling. Ceiling is ALL_PERMISSIONS for
      admins, else the role perms. Result never contains the "admin" sentinel.
    """
    if scopes is None:
        return role_perms | ({"admin"} if is_admin else set())
    scope_perms = expand_scopes(scopes)
    ceiling = set(ALL_PERMISSIONS) if is_admin else role_perms
    return scope_perms & ceiling


def effective_permissions(user: User) -> set[str]:
    """Global permissions a request actually has, accounting for a PAT scope."""
    role_perms = _all_role_permissions(user)
    is_admin = user.is_admin or "admin" in role_perms
    scopes = getattr(user, "active_token_scopes", None)
    return _apply_token_scope(role_perms, is_admin, scopes)


def effective_scoped_permissions(
    user: User, scope_type: str, scope_id: uuid.UUID | None
) -> set[str]:
    """Resource-scoped permissions a request actually has, accounting for a PAT scope."""
    role_perms = _scoped_role_permissions(user, scope_type, scope_id)
    is_admin = user.is_admin or "admin" in role_perms
    scopes = getattr(user, "active_token_scopes", None)
    return _apply_token_scope(role_perms, is_admin, scopes)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # ── PAT path ──────────────────────────────────────────────────────
    if is_pat(token):
        token_hash = hash_pat(token)
        result = await db.execute(
            select(ApiToken).where(
                ApiToken.token_hash == token_hash,
                ApiToken.is_active.is_(True),
            )
        )
        api_token = result.scalar_one_or_none()
        if api_token is None:
            raise credentials_exception

        # Check expiry.
        if (
            api_token.expires_at is not None
            and api_token.expires_at < datetime.now(timezone.utc)
        ):
            raise credentials_exception

        # Touch last_used_at (fire-and-forget, don't block auth).
        await db.execute(
            update(ApiToken)
            .where(ApiToken.id == api_token.id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await db.commit()

        user_result = await db.execute(
            select(User)
            .options(selectinload(User.user_roles).selectinload(UserRole.role))
            .where(User.id == api_token.user_id)
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            raise credentials_exception
        return user

    # ── JWT path (original) ───────────────────────────────────────────
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
    """Check that the user has admin-level access.

    Accepts both the legacy ``is_admin`` boolean **and** the RBAC ``admin``
    permission so the migration to pure RBAC is non-breaking.
    """
    if current_user.is_admin:
        return current_user
    perms = _collect_permissions(current_user)
    if "admin" in perms:
        return current_user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
    )


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
            import asyncio
            from app.core.audit import record as audit_record

            asyncio.ensure_future(audit_record(
                action="permission_denied",
                actor_id=current_user.id,
                metadata={"permission": permission},
            ))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required",
            )
        return current_user

    return _check


def check_scoped_permission(
    user: User,
    permission: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
) -> None:
    """Raise 403 if the user lacks *permission* in the given scope.

    Call from endpoints after resolving the resource.
    """
    if user.is_admin:
        return
    perms = _collect_scoped_permissions(user, scope_type, scope_id)
    if permission not in perms and "admin" not in perms:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permission '{permission}' required for this {scope_type}",
        )


def get_user_primary_role_name(user: User) -> str | None:
    """Return the name of the user's first global role, or None."""
    if user.is_admin:
        return "admin"
    for ur in user.user_roles:
        if ur.scope_type == "global" and ur.role:
            return ur.role.name
    return None
