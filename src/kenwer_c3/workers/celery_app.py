"""Celery application for Channel 3 asynchronous learning jobs."""

from celery import Celery

from kenwer_c3.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "kenwer_c3",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["kenwer_c3.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
)


@celery_app.task(name="kenwer_c3.workers.ping")
def ping() -> dict[str, str]:
    """Smoke task used by infrastructure checks."""
    return {"status": "pong"}
