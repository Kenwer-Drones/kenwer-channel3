"""Celery tasks for Channel 3 learning jobs."""

from __future__ import annotations

import logging
import uuid

from kenwer_c3.db.base import get_session_factory
from kenwer_c3.learning.pipeline import LearningPipeline
from kenwer_c3.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="kenwer_c3.workers.run_learning_job",
    bind=True,
    max_retries=2,
    default_retry_delay=5,
    acks_late=True,
)
def run_learning_job(self, job_id: str) -> dict:
    """Execute C1–C6 for a learning job. Never writes consolidated memory."""
    session = get_session_factory()()
    try:
        job_uuid = uuid.UUID(job_id)
        pipeline = LearningPipeline(session)
        job = pipeline.run(job_uuid)
        session.commit()
        return {
            "job_id": str(job.id),
            "status": job.status.value if hasattr(job.status, "value") else str(job.status),
            "current_stage": job.current_stage,
            "attempt_count": job.attempt_count,
            "error_message": job.error_message,
        }
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        logger.exception("Learning job %s failed in worker", job_id)
        # Retry transient failures; pipeline itself marks terminal failure states
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {
                "job_id": job_id,
                "status": "failed",
                "error_message": str(exc),
            }
    finally:
        session.close()
