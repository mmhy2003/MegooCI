"""Seeding helpers for MCP tests: users with roles, and API tokens."""
import uuid


async def seed_member(db, permissions, *, project_id=None, is_admin=False) -> uuid.UUID:
    """Insert a user holding *permissions* globally, or on *project_id* only."""
    from app.models.role import Role, UserRole
    from app.models.user import User

    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="U",
                is_active=True, is_admin=is_admin)
    role = Role(id=uuid.uuid4(), name=f"r-{uuid.uuid4().hex[:8]}",
                permissions=list(permissions))
    db.add_all([user, role])
    await db.flush()
    db.add(UserRole(
        id=uuid.uuid4(), user_id=user.id, role_id=role.id,
        scope_type="project" if project_id else "global", scope_id=project_id,
    ))
    await db.flush()
    return user.id


async def seed_token(db, user_id, scopes=None, *, expires_at=None, is_active=True) -> str:
    """Insert an API token for *user_id* and return its raw value."""
    from app.core.security import generate_pat, hash_pat, pat_hint
    from app.models.api_token import ApiToken

    raw = generate_pat()
    db.add(ApiToken(
        id=uuid.uuid4(), user_id=user_id, name="t", token_hash=hash_pat(raw),
        token_hint=pat_hint(raw), scopes=scopes, expires_at=expires_at,
        is_active=is_active,
    ))
    await db.flush()
    return raw
