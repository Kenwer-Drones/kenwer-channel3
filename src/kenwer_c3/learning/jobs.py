"""Learning job creation and status (authenticated API support)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import LearningJob, LearningJobStatus, MissionEpisode
from kenwer_c3.domain.learning import LearningJobCreate, LearningJobRead
from kenwer_c3.memory.audit import write_audit
from kenwer_c3.db.models import MemoryFamily


class LearningJobService:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def create(self, payload: LearningJobCreate) -> LearningJobRead:
        episode = self.session.execute(
            select(MissionEpisode).where(
                MissionEpisode.id == payload.episode_id,
                MissionEpisode.tenant_id == self.principal.tenant_id,
            )
        ).scalar_one_or_none()
        if episode is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        self.principal.assert_identity_access(episode.cognitive_identity_id)

        if payload.idempotency_key:
            existing = self.session.execute(
                select(LearningJob).where(
                    LearningJob.tenant_id == self.principal.tenant_id,
                    LearningJob.idempotency_key == payload.idempotency_key,
                )
            ).scalar_one_or_none()
            if existing is not None:
                return LearningJobRead.model_validate(existing)

        # Reject if an active job already exists for this episode
        active = self.session.execute(
            select(LearningJob).where(
                LearningJob.tenant_id == self.principal.tenant_id,
                LearningJob.episode_id == episode.id,
                LearningJob.status.in_(
                    [LearningJobStatus.queued, LearningJobStatus.running]
                ),
            )
        ).scalar_one_or_none()
        if active is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An active learning job already exists for this episode",
            )

        job = LearningJob(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            episode_id=episode.id,
            cognitive_identity_id=episode.cognitive_identity_id,
            status=LearningJobStatus.queued,
            current_stage=None,
            stage_artifacts={},
            attempt_count=0,
            idempotency_key=payload.idempotency_key,
        )
        try:
            with self.session.begin_nested():
                self.session.add(job)
                self.session.flush()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Concurrent duplicate learning job rejected",
            ) from exc

        write_audit(
            self.session,
            tenant_id=self.principal.tenant_id,
            actor_key_id=self.principal.api_key_id,
            action="learning_job.create",
            resource_type="learning_job",
            resource_id=str(job.id),
            memory_family=MemoryFamily.episodic,
            details={"episode_id": str(episode.id)},
        )
        return LearningJobRead.model_validate(job)

    def get(self, job_id: uuid.UUID) -> LearningJobRead:
        job = self.session.execute(
            select(LearningJob).where(
                LearningJob.id == job_id,
                LearningJob.tenant_id == self.principal.tenant_id,
            )
        ).scalar_one_or_none()
        if job is None:
            raise HTTPException(status_code=404, detail="Learning job not found")
        self.principal.assert_identity_access(job.cognitive_identity_id)
        return LearningJobRead.model_validate(job)

    def attach_celery_task(self, job_id: uuid.UUID, task_id: str) -> None:
        job = self.session.get(LearningJob, job_id)
        if job is None or job.tenant_id != self.principal.tenant_id:
            return
        job.celery_task_id = task_id
        job.updated_at = datetime.now(timezone.utc)
        self.session.flush()
