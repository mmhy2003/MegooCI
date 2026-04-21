"""Lightweight audit logging helper.

Writes rows to the ``audit_log`` table for security-relevant actions
(login, permission checks, admin actions, etc.). Designed to be called
fire-and-forget so it never blocks request handling.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.audit import AuditLogEntry

logger = logging.getLogger(__name__)


async def record(
    *,
    action: str,
    actor_id: uuid.UUID | None = None,
    target_type: str = "",
    target_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Persist an audit log entry.

    When *db* is provided the entry joins the caller's transaction.
    Otherwise a new session is opened (and committed) automatically so
    the caller doesn't have to worry about session lifecycle.
    """
    entry = AuditLogEntry(
        action=action,
        actor_id=actor_id,
        target_type=target_type,
        target_id=target_id,
        metadata_json=metadata,
        ip_address=ip_address,
    )

    if db is not None:
        db.add(entry)
        return

    try:
        async with async_session() as session:
            session.add(entry)
            await session.commit()
    except Exception:
        logger.warning("Failed to write audit log entry: %s", action, exc_info=True)
