from pathlib import Path

from celery import Celery

from app.config import get_settings

settings = get_settings()

# Keep Celery Beat's schedule file inside the persistent storage volume so
# it doesn't end up in the source tree during local development (where the
# backend container bind-mounts ./backend:/app).
_beat_dir = Path(settings.MEGOOCI_STORAGE_ROOT) / "celery"
try:
    _beat_dir.mkdir(parents=True, exist_ok=True)
except (OSError, PermissionError):
    # Fall back to /tmp if the storage root isn't writable yet
    _beat_dir = Path("/tmp/megooci-celery")
    _beat_dir.mkdir(parents=True, exist_ok=True)

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
    beat_schedule_filename=str(_beat_dir / "celerybeat-schedule"),
)

celery_app.autodiscover_tasks(["app.tasks"], related_name="build_tasks")
celery_app.autodiscover_tasks(["app.tasks"], related_name="registry_tasks")

from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    "registry-gc": {
        "task": "megooci.registry_gc",
        "schedule": crontab(hour=3, minute=0),
        "options": {"queue": "megooci"},
    },
}
