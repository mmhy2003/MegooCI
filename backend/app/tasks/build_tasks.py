import asyncio
import uuid

from app.tasks.celery_app import celery_app


def _make_session_factory():
    """Create a fresh async engine + session factory for this worker process.

    Each celery worker fork needs its own engine so the connection pool is
    bound to the current event loop rather than the parent process's loop.
    """
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.MEGOOCI_DATABASE_URL, echo=False, future=True)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@celery_app.task(name="megooci.run_build", bind=True, max_retries=0)
def run_build(self, build_id: str) -> dict:
    """Celery task that executes a build via the async build executor."""
    from app.services.build_executor import execute_build

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        session_factory = _make_session_factory()
        loop.run_until_complete(
            execute_build(uuid.UUID(build_id), session_factory=session_factory)
        )
        return {"build_id": build_id, "status": "completed"}
    except Exception as exc:
        return {"build_id": build_id, "status": "error", "error": str(exc)}
    finally:
        loop.close()
