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
def run_build(self, build_id: str, claimed_agent_id: str | None = None) -> dict:
    """Celery task that executes a build via the async build executor."""
    from app.services.build_executor import execute_build

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        session_factory = _make_session_factory()
        agent_uuid = uuid.UUID(claimed_agent_id) if claimed_agent_id else None
        loop.run_until_complete(
            execute_build(
                uuid.UUID(build_id),
                session_factory=session_factory,
                claimed_agent_id=agent_uuid,
            )
        )
        return {"build_id": build_id, "status": "completed"}
    except Exception as exc:
        return {"build_id": build_id, "status": "error", "error": str(exc)}
    finally:
        loop.close()


@celery_app.task(name="megooci.reconcile_and_dispatch", bind=True, max_retries=0)
def reconcile_and_dispatch(self) -> dict:
    """Periodic safety net (Celery Beat): clear leaked agent reservations and
    re-run the dispatcher so no pending build or freed agent stays stranded by
    a missed dispatch edge."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.config import get_settings
    from app.services.agent_dispatcher import reconcile_and_dispatch as _run

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # Fresh engine bound to this task's loop (see _make_session_factory). This
    # task fires every 30s, so — unlike run_build — we must dispose the engine
    # afterwards or we'd leak a connection pool on every tick.
    engine = create_async_engine(get_settings().MEGOOCI_DATABASE_URL, echo=False, future=True)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        loop.run_until_complete(_run(session_factory=session_factory))
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        loop.run_until_complete(engine.dispose())
        loop.close()

