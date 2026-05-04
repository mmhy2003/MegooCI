"""Seed the database with an initial admin user on first startup.

Activated only when:
1. ``MEGOOCI_ADMIN_EMAIL`` and ``MEGOOCI_ADMIN_PASSWORD`` are both set in the
   environment / ``.env`` file.
2. The ``users`` table is completely empty (clean first startup).

If both conditions are met, a single admin user is created with the ``admin``
role assigned globally. On all subsequent startups the function is a no-op
because the table is no longer empty.
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import hash_password
from app.models.role import Role, UserRole
from app.models.user import User

logger = logging.getLogger(__name__)


async def seed_admin_user(db: AsyncSession) -> None:
    """Create the initial admin user if the DB is empty and env vars are set."""
    settings = get_settings()

    email = settings.MEGOOCI_ADMIN_EMAIL.strip()
    password = settings.MEGOOCI_ADMIN_PASSWORD.strip()

    if not email or not password:
        return  # Not configured — skip silently.

    # Only seed when the users table is completely empty.
    user_count = await db.scalar(select(func.count()).select_from(User))
    if user_count and user_count > 0:
        return

    if len(password) < 8:
        logger.warning(
            "MEGOOCI_ADMIN_PASSWORD is shorter than 8 characters — "
            "skipping admin seed for security"
        )
        return

    logger.info("Seeding initial admin user: %s", email)

    user = User(
        email=email,
        name=email.split("@")[0],
        hashed_password=hash_password(password),
        is_admin=True,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    # Assign the "admin" role globally.
    role_result = await db.execute(select(Role).where(Role.name == "admin"))
    role = role_result.scalar_one_or_none()
    if role:
        db.add(UserRole(user_id=user.id, role_id=role.id, scope_type="global"))

    await db.commit()
    logger.info("Admin user created successfully: %s", email)
