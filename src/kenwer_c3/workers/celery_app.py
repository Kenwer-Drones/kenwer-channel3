"""Celery application skeleton (learning tasks land in later phases)."""

from celery import Celery

from kenwer_c3.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "kenwer_c3",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)


@celery_app.task(name="kenwer_c3.workers.ping")
def ping() -> dict[str, str]:
    """Smoke task used by Phase 1 infrastructure checks."""
    return {"status": "pong"}
