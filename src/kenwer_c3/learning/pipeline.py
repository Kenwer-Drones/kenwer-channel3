"""Orchestrate C1→C2→C3 for a learning job. Does not touch consolidated memory."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from kenwer_c3.db.models import DiscrepancyReport, LearningJob, LearningJobStatus
from kenwer_c3.learning.c1_reconstruction import EpisodeReconstructionService
from kenwer_c3.learning.c2_expectations import ExpectationRetrievalService
from kenwer_c3.learning.c3_evaluation import OutcomeEvaluationService


class LearningPipeline:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _store_artifacts(job: LearningJob, artifacts: dict) -> None:
        job.stage_artifacts = dict(artifacts)
        flag_modified(job, "stage_artifacts")

    def run(self, job_id: uuid.UUID) -> LearningJob:
        job = (
            self.session.query(LearningJob)
            .filter(LearningJob.id == job_id)
            .with_for_update()
            .one_or_none()
        )
        if job is None:
            raise ValueError(f"Learning job {job_id} not found")

        # Idempotent: finished jobs are not re-executed
        if job.status in (
            LearningJobStatus.completed,
            LearningJobStatus.failed,
            LearningJobStatus.failed_soft,
        ):
            return job

        # queued or running (safe retry after worker crash) may proceed under row lock
        artifacts: dict = dict(job.stage_artifacts or {})
        job.status = LearningJobStatus.running
        job.attempt_count = (job.attempt_count or 0) + 1
        job.current_stage = "C1"
        job.error_message = None
        self.session.flush()

        try:
            reconstruction = EpisodeReconstructionService(
                self.session, job.tenant_id
            ).reconstruct(job.episode_id)
            artifacts["C1"] = reconstruction.model_dump(mode="json")
            self._store_artifacts(job, artifacts)
            job.current_stage = "C2"
            self.session.flush()

            baseline = ExpectationRetrievalService(
                self.session, job.tenant_id
            ).retrieve(job.episode_id)
            artifacts["C2"] = baseline.model_dump(mode="json")
            self._store_artifacts(job, artifacts)
            job.current_stage = "C3"
            self.session.flush()

            evaluation = OutcomeEvaluationService().evaluate(reconstruction, baseline)
            artifacts["C3"] = evaluation.model_dump(mode="json")
            self._store_artifacts(job, artifacts)

            self.session.query(DiscrepancyReport).filter(
                DiscrepancyReport.learning_job_id == job.id
            ).delete(synchronize_session=False)

            for report in evaluation.reports:
                self.session.add(
                    DiscrepancyReport(
                        id=uuid.uuid4(),
                        tenant_id=job.tenant_id,
                        learning_job_id=job.id,
                        episode_id=job.episode_id,
                        metric_name=report.metric_name,
                        expected_value=report.expected_value,
                        observed_value=report.observed_value,
                        discrepancy=report.discrepancy,
                        result_status=report.result_status.value,
                        unit=report.unit,
                        comparison_operator=report.comparison_operator,
                        evidence_refs=report.evidence_refs,
                        measurement_quality=report.measurement_quality,
                        uncertainty=report.uncertainty,
                        missing_evidence=report.missing_evidence,
                    )
                )

            job.status = LearningJobStatus.completed
            job.current_stage = "C3_done"
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = None
            self.session.flush()
            return job

        except HTTPException as exc:
            return self._fail(
                job_id,
                artifacts,
                soft=(exc.status_code == 409),
                message=f"HTTPException({exc.status_code}): {exc.detail}",
            )
        except Exception as exc:  # noqa: BLE001
            return self._fail(
                job_id,
                artifacts,
                soft=False,
                message=f"{type(exc).__name__}: {exc}",
            )

    def _fail(
        self,
        job_id: uuid.UUID,
        artifacts: dict,
        *,
        soft: bool,
        message: str,
    ) -> LearningJob:
        self.session.rollback()
        job = self.session.get(LearningJob, job_id)
        if job is None:
            raise ValueError(f"Learning job {job_id} disappeared after failure")
        job.status = (
            LearningJobStatus.failed_soft if soft else LearningJobStatus.failed
        )
        job.error_message = message
        self._store_artifacts(job, artifacts)
        job.completed_at = datetime.now(timezone.utc)
        self.session.flush()
        return job
