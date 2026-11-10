"""Learning job APIs — enqueue C1–C6 evaluation (proposals only; no auto-publish)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from kenwer_c3.auth.context import Principal, get_db_session, require_roles
from kenwer_c3.db.models import LearningJob
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.learning import LearningJobCreate, LearningJobRead
from kenwer_c3.learning.jobs import LearningJobService
from kenwer_c3.learning.pipeline import LearningPipeline
from kenwer_c3.settings import get_settings
from kenwer_c3.workers.tasks import run_learning_job

router = APIRouter(prefix="/v1/learning", tags=["learning"])


@router.post(
    "/jobs",
    response_model=LearningJobRead,
    summary="Enqueue a C1–C6 learning/evaluation job for an episode",
)
def create_learning_job(
    body: LearningJobCreate,
    principal: Principal = Depends(require_roles(Role.admin, Role.channel2_ingest)),
    session: Session = Depends(get_db_session),
) -> LearningJobRead:
    settings = get_settings()
    svc = LearningJobService(session, principal)
    job = svc.create(body)

    if settings.celery_task_always_eager:
        # Deterministic inline execution for tests / local without a worker
        LearningPipeline(session).run(job.id)
    else:
        async_result = run_learning_job.delay(str(job.id))
        row = session.get(LearningJob, job.id)
        if row is not None:
            row.celery_task_id = async_result.id
            session.flush()

    return LearningJobService(session, principal).get(job.id)


@router.get(
    "/jobs/{job_id}",
    response_model=LearningJobRead,
    summary="Get learning job status and stage artifacts",
)
def get_learning_job(
    job_id: UUID,
    principal: Principal = Depends(
        require_roles(
            Role.admin,
            Role.channel2_ingest,
            Role.channel1_retrieve,
            Role.reviewer,
        )
    ),
    session: Session = Depends(get_db_session),
) -> LearningJobRead:
    return LearningJobService(session, principal).get(job_id)
