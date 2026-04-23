"""Celery tasks for registry maintenance — garbage collection of
unreferenced blobs and enforcement of retention policies.
"""

import asyncio
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _make_session_factory():
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.MEGOOCI_DATABASE_URL, echo=False, future=True)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _gc_unreferenced_blobs() -> dict:
    """Two-phase mark-and-sweep: find blob digests referenced by images,
    then delete any on-disk blob that is not referenced."""
    from sqlalchemy import select
    from app.models.registry import ContainerImage
    from app.services import registry_storage as storage

    session_factory = _make_session_factory()

    async with session_factory() as db:
        result = await db.execute(select(ContainerImage.digest))
        referenced = {row[0] for row in result.all()}

        config_result = await db.execute(
            select(ContainerImage.config_digest).where(
                ContainerImage.config_digest.isnot(None)
            )
        )
        for row in config_result.all():
            if row[0]:
                referenced.add(row[0])

    on_disk = set(storage.list_all_blobs())
    unreferenced = on_disk - referenced

    deleted_count = 0
    freed_bytes = 0
    for digest in unreferenced:
        size = storage.blob_size(digest)
        if storage.delete_blob(digest):
            deleted_count += 1
            freed_bytes += size

    logger.info(
        "Registry GC complete: deleted %d unreferenced blobs, freed %d bytes",
        deleted_count,
        freed_bytes,
    )
    return {"deleted_blobs": deleted_count, "freed_bytes": freed_bytes}


@celery_app.task(name="megooci.registry_gc", bind=True, max_retries=0)
def registry_gc(self) -> dict:
    """Run garbage collection of unreferenced blobs."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_gc_unreferenced_blobs())
    finally:
        loop.close()
