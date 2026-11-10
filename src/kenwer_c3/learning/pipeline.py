"""Orchestrate C1→C6 for a learning job.

C6 emits belief proposals only. Consolidated memory is never written by this
pipeline — publication requires separate validation + authorized human review.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from kenwer_c3.db.models import DiscrepancyReport, LearningJob, LearningJobStatus
from kenwer_c3.domain.learning import EpisodeReconstruction, OutcomeEvaluationResult
from kenwer_c3.learning.c1_reconstruction import EpisodeReconstructionService
from kenwer_c3.learning.c2_expectations import ExpectationRetrievalService
from kenwer_c3.learning.c3_evaluation import OutcomeEvaluationService
from kenwer_c3.learning.c4_attribution import AttributionAnalysisService
from kenwer_c3.learning.c5_lessons import CandidateLessonService
from kenwer_c3.learning.c6_reconciliation import BeliefReconciliationService
from kenwer_c3.learning.evidence_context import EvidenceContextBuilder
from kenwer_c3.llm.base import ReasoningProviderError
from kenwer_c3.llm.factory import get_reasoning_provider


class LearningPipeline:
    def __init__(self, session: Session, reasoning_provider=None):
        self.session = session
        self.provider = reasoning_provider or get_reasoning_provider()

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

        if job.status in (
            LearningJobStatus.completed,
            LearningJobStatus.failed,
            LearningJobStatus.failed_soft,
        ):
            return job

        artifacts: dict = dict(job.stage_artifacts or {})
        job.status = LearningJobStatus.running
        job.attempt_count = (job.attempt_count or 0) + 1
        job.current_stage = "C1"
        job.error_message = None
        self.session.flush()

        reconstruction: EpisodeReconstruction | None = None
        evaluation: OutcomeEvaluationResult | None = None

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
            self.session.flush()

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

        # C4–C5: provider failures must not corrupt C1–C3 / episodic memory
        assert reconstruction is not None and evaluation is not None
        try:
            job.current_stage = "C4"
            self.session.flush()
            evidence_context = EvidenceContextBuilder(
                self.session, job.tenant_id
            ).build(
                episode_id=job.episode_id,
                reconstruction=reconstruction,
                evaluation=evaluation,
            )
            artifacts["evidence_context"] = {
                "allowed_evidence_ids": evidence_context.get("allowed_evidence_ids"),
                "mission_type": evidence_context.get("mission_type"),
                "counts": {
                    "episodic_events": len(evidence_context.get("episodic_events", [])),
                    "persistent_facts": len(evidence_context.get("persistent_facts", [])),
                    "approved_beliefs": len(evidence_context.get("approved_beliefs", [])),
                },
                # Do not persist full untrusted payloads into job artifacts by default
            }
            self._store_artifacts(job, artifacts)

            c4 = AttributionAnalysisService(
                self.session, job.tenant_id, self.provider
            ).run(
                job_id=job.id,
                episode_id=job.episode_id,
                reconstruction=reconstruction,
                evaluation=evaluation,
                evidence_context=evidence_context,
            )
            artifacts["C4"] = c4
            self._store_artifacts(job, artifacts)
            job.current_stage = "C5"
            self.session.flush()

            # Primary discrepancy for lesson generation: first fail, else first unknown
            primary = next(
                (
                    r.model_dump(mode="json")
                    for r in evaluation.reports
                    if r.result_status.value == "fail"
                ),
                None,
            ) or (
                evaluation.reports[0].model_dump(mode="json")
                if evaluation.reports
                else {"metric_name": "unknown"}
            )

            c5 = CandidateLessonService(
                self.session, job.tenant_id, self.provider
            ).run(
                job_id=job.id,
                episode_id=job.episode_id,
                evidence_context=evidence_context,
                c4_result=c4,
                primary_discrepancy=primary,
            )
            artifacts["C5"] = c5
            self._store_artifacts(job, artifacts)
            job.current_stage = "C6"
            self.session.flush()

            # C6: reconcile → proposals only (no publication)
            c6 = BeliefReconciliationService(self.session, job.tenant_id).run(
                job_id=job.id,
                cognitive_identity_id=job.cognitive_identity_id,
                evidence_context=evidence_context,
            )
            artifacts["C6"] = c6
            self._store_artifacts(job, artifacts)

            job.status = LearningJobStatus.completed
            job.current_stage = "C6_done"
            job.completed_at = datetime.now(timezone.utc)
            job.error_message = None
            self.session.flush()
            return job

        except ReasoningProviderError as exc:
            artifacts["C4_C5_error"] = str(exc)
            self._store_artifacts(job, artifacts)
            job.status = LearningJobStatus.failed_soft
            job.error_message = f"ReasoningProviderError: {exc}"
            job.completed_at = datetime.now(timezone.utc)
            job.current_stage = "C4_C5_failed_soft"
            self.session.flush()
            return job
        except Exception as exc:  # noqa: BLE001
            # Soft-fail: keep C1–C3 committed in this session
            stage = job.current_stage or "C4_C6"
            artifacts["C4_C6_error"] = f"{type(exc).__name__}: {exc}"
            self._store_artifacts(job, artifacts)
            job.status = LearningJobStatus.failed_soft
            job.error_message = f"{type(exc).__name__}: {exc}"
            job.completed_at = datetime.now(timezone.utc)
            job.current_stage = f"{stage}_failed_soft"
            self.session.flush()
            return job

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
