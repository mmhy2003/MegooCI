from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "megooci",
    broker=settings.MEGOOCI_REDIS_URL,
    backend="rpc://",
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_default_queue="megooci",
)

celery_app.autodiscover_tasks(["app.tasks"])
