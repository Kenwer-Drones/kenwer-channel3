"""C4 - Evidence & Attribution Analysis (hypotheses only)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from kenwer_c3.db.models import AttributionHypothesis, DiscrepancyReport
from kenwer_c3.domain.learning import OutcomeEvaluationResult
from kenwer_c3.learning.evidence_context import EvidenceContextBuilder
from kenwer_c3.learning.evidence_validation import filter_attribution_to_context
from kenwer_c3.llm.base import ReasoningProvider, ReasoningProviderError
from kenwer_c3.llm.schemas import AttributionAnalysisOut


class AttributionAnalysisService:
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
        reconstruction,
        evaluation: OutcomeEvaluationResult,
        evidence_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        context = evidence_context or EvidenceContextBuilder(
            self.session, self.tenant_id
        ).build(
            episode_id=episode_id,
            reconstruction=reconstruction,
            evaluation=evaluation,
        )
        allowed = set(context.get("allowed_evidence_ids", []))

        # Clear prior hypotheses for idempotent re-run
        self.session.query(AttributionHypothesis).filter(
            AttributionHypothesis.learning_job_id == job_id
        ).delete(synchronize_session=False)

        discrepancy_rows = (
            self.session.query(DiscrepancyReport)
            .filter(DiscrepancyReport.learning_job_id == job_id)
            .all()
        )
        by_metric = {r.metric_name: r for r in discrepancy_rows}

        analyses: list[dict[str, Any]] = []
        group_id = uuid.uuid4()

        actionable = [
            r
            for r in evaluation.reports
            if r.result_status.value in ("fail", "unknown")
        ]
        if not actionable:
            return {
                "analyses": [],
                "abstain": True,
                "abstain_reason": "No fail/unknown discrepancies to attribute",
                "provider_metadata": [],
            }

        provider_meta: list[dict[str, Any]] = []
        for report in actionable:
            try:
                raw, meta = self.provider.analyze_attribution(
                    evidence_context=context,
                    discrepancy=report.model_dump(mode="json"),
                )
            except ReasoningProviderError as exc:
                analyses.append(
                    {
                        "metric_name": report.metric_name,
                        "abstain": True,
                        "abstain_reason": f"provider_error: {exc}",
                        "hypotheses": [],
                    }
                )
                provider_meta.append({"error": str(exc), "metric": report.metric_name})
                continue

            validated = filter_attribution_to_context(raw, allowed)
            provider_meta.append(meta)
            disc_row = by_metric.get(report.metric_name)

            persisted = []
            for hypo in validated.hypotheses:
                row = AttributionHypothesis(
                    id=uuid.uuid4(),
                    tenant_id=self.tenant_id,
                    learning_job_id=job_id,
                    discrepancy_report_id=disc_row.id if disc_row else None,
                    hypothesis=hypo.statement,
                    competing_group_id=group_id,
                    # Stored numeric score is uncalibrated - see evidence_refs
                    confidence=hypo.model_confidence_uncalibrated,
                    is_hypothesis_only=True,
                    model_id=meta.get("model_id"),
                    prompt_hash=meta.get("prompt_hash"),
                    evidence_refs={
                        "supporting": [c.model_dump() for c in hypo.supporting_evidence],
                        "contradictory": [
                            c.model_dump() for c in hypo.contradictory_evidence
                        ],
                        "missing": hypo.missing_evidence,
                        "uncertainty": hypo.uncertainty,
                        "correlation_only": hypo.correlation_only,
                        "claims_causation": hypo.claims_causation,
                        "model_confidence_uncalibrated": hypo.model_confidence_uncalibrated,
                        "evidence_support_label": hypo.evidence_support_label,
                        "confidence_basis": "uncalibrated_model",
                        "confidence_note": (
                            "Numeric score is an uncalibrated model assignment, "
                            "not a calibrated probability."
                        ),
                    },
                )
                self.session.add(row)
                persisted.append(
                    {
                        "id": str(row.id),
                        "statement": hypo.statement,
                        "model_confidence_uncalibrated": hypo.model_confidence_uncalibrated,
                        "evidence_support_label": hypo.evidence_support_label,
                        "confidence_basis": "uncalibrated_model",
                        "supporting_evidence": [
                            c.model_dump() for c in hypo.supporting_evidence
                        ],
                        "contradictory_evidence": [
                            c.model_dump() for c in hypo.contradictory_evidence
                        ],
                        "missing_evidence": hypo.missing_evidence,
                        "uncertainty": hypo.uncertainty,
                        "correlation_only": hypo.correlation_only,
                        "is_hypothesis_only": True,
                    }
                )

            analyses.append(
                {
                    "metric_name": report.metric_name,
                    "abstain": validated.abstain,
                    "abstain_reason": validated.abstain_reason,
                    "hypotheses": persisted,
                    "competing_group_id": str(group_id),
                }
            )

        self.session.flush()
        return {
            "analyses": analyses,
            "evidence_context_summary": {
                "allowed_evidence_count": len(allowed),
                "episodic_events": len(context.get("episodic_events", [])),
                "persistent_facts": len(context.get("persistent_facts", [])),
                "approved_beliefs": len(context.get("approved_beliefs", [])),
            },
            "provider_metadata": provider_meta,
        }
