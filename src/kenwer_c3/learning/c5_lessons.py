"""C5 — Candidate Lesson Generation (never writes consolidated/persistent memory)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from kenwer_c3.db.models import CandidateLesson, SafetyImpact
from kenwer_c3.learning.evidence_validation import filter_lessons_to_context
from kenwer_c3.llm.base import ReasoningProvider, ReasoningProviderError
from kenwer_c3.llm.schemas import AttributionAnalysisOut, AttributionHypothesisOut, EvidenceCitation


class CandidateLessonService:
    def __init__(
        self,
        session: Session,
        tenant_id: uuid.UUID,
        provider: ReasoningProvider,
    ):
        self.session = session
        self.tenant_id = tenant_id
        self.provider = provider

    def run(
        self,
        *,
        job_id: uuid.UUID,
        episode_id: uuid.UUID,
        evidence_context: dict[str, Any],
        c4_result: dict[str, Any],
        primary_discrepancy: dict[str, Any],
    ) -> dict[str, Any]:
        allowed = set(evidence_context.get("allowed_evidence_ids", []))

        self.session.query(CandidateLesson).filter(
            CandidateLesson.learning_job_id == job_id
        ).delete(synchronize_session=False)

        # Rebuild AttributionAnalysisOut from C4 for provider input
        hypos: list[AttributionHypothesisOut] = []
        abstain = bool(c4_result.get("abstain"))
        abstain_reason = c4_result.get("abstain_reason")
        for analysis in c4_result.get("analyses", []):
            if analysis.get("abstain"):
                abstain = abstain or True
                abstain_reason = abstain_reason or analysis.get("abstain_reason")
            for h in analysis.get("hypotheses", []):
                score = h.get(
                    "model_confidence_uncalibrated",
                    h.get("confidence", 0.0),
                )
                hypos.append(
                    AttributionHypothesisOut(
                        statement=h["statement"],
                        model_confidence_uncalibrated=float(score),
                        evidence_support_label=h.get("evidence_support_label"),
                        supporting_evidence=[
                            EvidenceCitation(**c)
                            for c in h.get("supporting_evidence", [])
                        ],
                        contradictory_evidence=[
                            EvidenceCitation(**c)
                            for c in h.get("contradictory_evidence", [])
                        ],
                        missing_evidence=h.get("missing_evidence", []),
                        uncertainty=h.get("uncertainty", ""),
                        claims_causation=False,
                        correlation_only=True,
                    )
                )

        attribution = AttributionAnalysisOut(
            discrepancy_metric=primary_discrepancy.get("metric_name", "unknown"),
            hypotheses=hypos,
            abstain=abstain and not hypos,
            abstain_reason=abstain_reason,
        )

        try:
            raw, meta = self.provider.generate_lessons(
                evidence_context=evidence_context,
                attribution=attribution,
                discrepancy=primary_discrepancy,
            )
        except ReasoningProviderError as exc:
            self.session.flush()
            return {
                "lessons": [],
                "abstain": True,
                "abstain_reason": f"provider_error: {exc}",
                "provider_metadata": {"error": str(exc)},
            }

        validated = filter_lessons_to_context(raw, allowed)
        persisted = []
        for lesson in validated.lessons:
            try:
                safety = SafetyImpact(lesson.safety_impact)
            except ValueError:
                safety = SafetyImpact.operational
            row = CandidateLesson(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                learning_job_id=job_id,
                episode_id=episode_id,
                statement=lesson.claim,
                applicability={
                    **lesson.applicability,
                    "limitations": lesson.limitations,
                    "uncertainty": lesson.uncertainty,
                },
                evidence_refs={
                    "supporting_evidence_ids": lesson.supporting_evidence_ids,
                    "contradictory_evidence_ids": lesson.contradictory_evidence_ids,
                    "model_confidence_uncalibrated": lesson.model_confidence_uncalibrated,
                    "evidence_support_label": lesson.evidence_support_label,
                    "confidence_basis": "uncalibrated_model",
                    "confidence_note": (
                        "Numeric scores from the model are uncalibrated and are "
                        "not calibrated probabilities."
                    ),
                },
                safety_impact=safety,
                is_hypothesis_only=True,
                model_id=meta.get("model_id"),
                prompt_hash=meta.get("prompt_hash"),
            )
            self.session.add(row)
            persisted.append(
                {
                    "id": str(row.id),
                    "claim": lesson.claim,
                    "applicability": lesson.applicability,
                    "supporting_evidence_ids": lesson.supporting_evidence_ids,
                    "contradictory_evidence_ids": lesson.contradictory_evidence_ids,
                    "model_confidence_uncalibrated": lesson.model_confidence_uncalibrated,
                    "evidence_support_label": lesson.evidence_support_label,
                    "confidence_basis": "uncalibrated_model",
                    "limitations": lesson.limitations,
                    "uncertainty": lesson.uncertainty,
                    "is_hypothesis_only": True,
                    "safety_impact": safety.value,
                }
            )

        self.session.flush()
        return {
            "lessons": persisted,
            "abstain": validated.abstain,
            "abstain_reason": validated.abstain_reason,
            "provider_metadata": meta,
            "consolidated_memory_written": False,
            "persistent_memory_written": False,
        }
