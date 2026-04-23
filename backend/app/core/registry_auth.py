"""Authentication helpers for the Docker / OCI registry.

Docker clients use the ``Bearer`` token flow described in the Docker
Registry Token Authentication specification:

1. Client hits ``/v2/`` → gets ``401`` with a ``Www-Authenticate`` header
   pointing to the token endpoint.
2. Client requests ``GET /v2/token?scope=...&service=...`` with Basic auth.
3. Server validates credentials and returns a short-lived JWT.
4. Client uses the JWT in ``Authorization: Bearer <token>`` for all
   subsequent requests.

This module handles credential validation for user logins (Basic auth
with username/password), deploy tokens, and build-scoped tokens.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.security import verify_password
from app.models.registry import RegistryDeployToken
from app.models.role import UserRole
from app.models.user import User


_REGISTRY_TOKEN_EXPIRY = timedelta(minutes=30)


async def authenticate_basic(
    db: AsyncSession,
    username: str,
    password: str,
) -> tuple[str | None, uuid.UUID | None, list[str]]:
    """Validate Basic-auth credentials against user accounts or deploy tokens.

    Returns ``(subject, actor_id, granted_actions)`` or ``(None, None, [])``
    on failure.
    """
    if username == "deploy-token":
        return await _auth_deploy_token(db, password)

    from sqlalchemy import or_

    result = await db.execute(
        select(User)
        .options(selectinload(User.user_roles).selectinload(UserRole.role))
        .where(or_(User.email == username, User.name == username))
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None, None, []
    if not verify_password(password, user.hashed_password):
        return None, None, []

    actions = _user_actions(user)
    return str(user.id), user.id, actions


async def _auth_deploy_token(
    db: AsyncSession, token: str
) -> tuple[str | None, uuid.UUID | None, list[str]]:
    from app.core.security import verify_password as _verify

    result = await db.execute(select(RegistryDeployToken).where(
        RegistryDeployToken.is_active.is_(True)
    ))
    for dt in result.scalars():
        if _verify(token, dt.token_hash):
            if dt.expires_at and dt.expires_at < datetime.now(timezone.utc):
                continue
            dt.last_used_at = datetime.now(timezone.utc)
            actions = ["pull"] if dt.scope == "pull" else ["pull", "push"]
            return f"deploy-token:{dt.id}", None, actions
    return None, None, []


def _user_actions(user: User) -> list[str]:
    if user.is_admin:
        return ["pull", "push", "delete"]
    perms: set[str] = set()
    for ur in user.user_roles:
        if ur.role and ur.role.permissions:
            perms.update(ur.role.permissions)
    actions = []
    if "admin" in perms or "registry.read" in perms:
        actions.append("pull")
    if "admin" in perms or "registry.push" in perms:
        actions.append("push")
    if "admin" in perms or "registry.manage" in perms:
        actions.append("delete")
    return actions or ["pull"]


def issue_registry_token(
    subject: str,
    actions: list[str],
    scope: str | None = None,
) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iss": "megooci-registry",
        "aud": settings.MEGOOCI_REGISTRY_HOST,
        "exp": now + _REGISTRY_TOKEN_EXPIRY,
        "iat": now,
        "nbf": now,
        "access": actions,
        "type": "registry",
    }
    if scope:
        payload["scope"] = scope
    return jwt.encode(
        payload,
        settings.MEGOOCI_JWT_SECRET,
        algorithm=settings.MEGOOCI_JWT_ALGORITHM,
    )


def decode_registry_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.MEGOOCI_JWT_SECRET,
            algorithms=[settings.MEGOOCI_JWT_ALGORITHM],
            audience=settings.MEGOOCI_REGISTRY_HOST,
        )
        if payload.get("type") != "registry":
            return None
        return payload
    except Exception:
        return None
