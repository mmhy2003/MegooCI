import asyncio
import uuid

from app.tasks.celery_app import celery_app


@celery_app.task(name="megooci.run_build", bind=True, max_retries=0)
def run_build(self, build_id: str) -> dict:
    """Celery task that executes a build via the async build executor."""
    from app.services.build_executor import execute_build

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(execute_build(uuid.UUID(build_id)))
        return {"build_id": build_id, "status": "completed"}
    except Exception as exc:
        return {"build_id": build_id, "status": "error", "error": str(exc)}
    finally:
        loop.close()
