import asyncio

from app.tasks.celery_app import celery_app


@celery_app.task(name="megooci.scheduled_backup", bind=True, max_retries=0)
def scheduled_backup(self) -> dict:
    """Periodic (Celery Beat): make the scheduled configuration backup when
    one is due. Whether one is due is decided from the backup settings, so
    the schedule can be changed without restarting anything."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.services.backup import service

    settings = get_settings()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # A fresh engine bound to this task's loop, disposed afterwards: this
    # task fires every few minutes and must not leak a connection pool.
    engine = create_async_engine(settings.MEGOOCI_DATABASE_URL, echo=False, future=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        outcome = loop.run_until_complete(
            service.run_scheduled(factory, settings.MEGOOCI_SECRET_KEY)
        )
        return {"outcome": outcome}
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        finally:
            loop.close()
