"""Deterministic MockReasoningProvider for unit/CI tests (no external API)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from kenwer_c3.llm.schemas import (
    PROMPT_VERSION_ATTRIBUTION,
    PROMPT_VERSION_LESSONS,
    AttributionAnalysisOut,
    AttributionHypothesisOut,
    CandidateLessonOut,
    CandidateLessonsOut,
    EvidenceCitation,
)


def _prompt_hash(version: str, payload: dict[str, Any]) -> str:
    raw = version + ":" + json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _ids(context: dict[str, Any]) -> set[str]:
    return {str(x["id"]) for x in context.get("evidence_items", []) if "id" in x}


class MockReasoningProvider:
    """Rule-based, deterministic attribution and lesson generation."""

    def __init__(self, model_id: str = "mock-reasoning-v1"):
        self._model_id = model_id

    @property
    def model_id(self) -> str:
        return self._model_id

    def analyze_attribution(
        self,
        *,
        evidence_context: dict[str, Any],
        discrepancy: dict[str, Any],
    ) -> tuple[AttributionAnalysisOut, dict[str, Any]]:
        allowed = _ids(evidence_context)
        metric = discrepancy.get("metric_name", "unknown")
        result_status = discrepancy.get("result_status")
        events = evidence_context.get("episodic_events", [])
        facts = evidence_context.get("persistent_facts", [])
        beliefs = evidence_context.get("approved_beliefs", [])

        wind_events = [e for e in events if e.get("event_type") == "observation.wind"]
        quality_events = [
            e for e in events if e.get("event_type") == "observation.image_quality"
        ]
        camera_facts = [
            f for f in facts if "thermal" in str(f.get("fact_key", "")).lower()
            or "camera" in str(f.get("fact_key", "")).lower()
        ]

        meta = {
            "model_id": self._model_id,
            "prompt_version": PROMPT_VERSION_ATTRIBUTION,
            "prompt_hash": _prompt_hash(
                PROMPT_VERSION_ATTRIBUTION, {"d": discrepancy, "n": len(allowed)}
            ),
            "provider": "mock",
        }

        # Pass / unknown / N/A → abstain (no discrepancy to explain)
        if result_status in ("pass", "not_applicable"):
            out = AttributionAnalysisOut(
                discrepancy_metric=metric,
                hypotheses=[],
                abstain=True,
                abstain_reason="No actionable discrepancy for attribution",
            )
            return out, meta

        if result_status == "unknown" and not quality_events and metric == "image_quality":
            out = AttributionAnalysisOut(
                discrepancy_metric=metric,
                hypotheses=[],
                abstain=True,
                abstain_reason="Insufficient evidence: missing image quality observation",
            )
            return out, meta

        if metric == "image_quality" and result_status == "fail":
            hypotheses: list[AttributionHypothesisOut] = []
            # Competing hypothesis 1: wind contribution (correlation, not causation)
            if wind_events:
                wid = str(wind_events[0]["id"])
                qid = str(quality_events[0]["id"]) if quality_events else None
                supporting = [EvidenceCitation(evidence_id=wid, note="wind observation")]
                if qid:
                    supporting.append(
                        EvidenceCitation(evidence_id=qid, note="degraded image quality")
                    )
                # Only cite ids that exist
                supporting = [c for c in supporting if c.evidence_id in allowed]
                hypotheses.append(
                    AttributionHypothesisOut(
                        statement=(
                            "Elevated wind may correlate with reduced thermal image "
                            "quality for this platform (correlation only)."
                        ),
                        model_confidence_uncalibrated=0.55,
                        evidence_support_label="moderate",
                        supporting_evidence=supporting,
                        contradictory_evidence=[],
                        missing_evidence=[]
                        if camera_facts
                        else ["camera_focus_setting", "lens_contamination_check"],
                        uncertainty=(
                            "Wind and image quality co-occur; causation is not established."
                        ),
                        claims_causation=False,
                        correlation_only=True,
                    )
                )
            else:
                # Missing wind measurement — still offer camera hypothesis with gaps
                pass

            # Competing hypothesis 2: camera / payload configuration
            if quality_events:
                qid = str(quality_events[0]["id"])
                supporting = (
                    [EvidenceCitation(evidence_id=qid, note="quality score below baseline")]
                    if qid in allowed
                    else []
                )
                contradictory: list[EvidenceCitation] = []
                if wind_events and str(wind_events[0]["id"]) in allowed:
                    # Wind present does not contradict camera hypothesis; leave empty
                    contradictory = []
                missing = []
                if not camera_facts:
                    missing.append("payload.thermal_camera_configuration")
                if not wind_events:
                    missing.append("observation.wind")
                hypotheses.append(
                    AttributionHypothesisOut(
                        statement=(
                            "Thermal camera configuration or focus may have been "
                            "suboptimal during inspection (hypothesis only)."
                        ),
                        model_confidence_uncalibrated=0.45 if wind_events else 0.4,
                        evidence_support_label="weak",
                        supporting_evidence=supporting,
                        contradictory_evidence=contradictory,
                        missing_evidence=missing,
                        uncertainty=(
                            "Camera settings were not directly measured in this episode."
                        ),
                        claims_causation=False,
                        correlation_only=True,
                    )
                )

            if beliefs:
                # Reference belief as contextual, not proof
                bid = str(beliefs[0]["id"])
                if bid in allowed and hypotheses:
                    hypotheses[0].supporting_evidence.append(
                        EvidenceCitation(
                            evidence_id=bid,
                            note="related approved belief (contextual, not causal proof)",
                        )
                    )

            if not hypotheses:
                out = AttributionAnalysisOut(
                    discrepancy_metric=metric,
                    hypotheses=[],
                    abstain=True,
                    abstain_reason="Insufficient evidence to form competing hypotheses",
                )
                return out, meta

            # If wind missing entirely for image_quality fail, mark first hypo missing wind
            if not wind_events and hypotheses:
                # Only camera hypo exists; abstain if we also lack quality event
                if not quality_events:
                    out = AttributionAnalysisOut(
                        discrepancy_metric=metric,
                        hypotheses=[],
                        abstain=True,
                        abstain_reason="Missing wind and image quality measurements",
                    )
                    return out, meta

            return (
                AttributionAnalysisOut(
                    discrepancy_metric=metric,
                    hypotheses=hypotheses,
                    abstain=False,
                ),
                meta,
            )

        # Default: abstain for unsupported metrics
        out = AttributionAnalysisOut(
            discrepancy_metric=metric,
            hypotheses=[],
            abstain=True,
            abstain_reason=f"No deterministic mock attribution rules for metric '{metric}'",
        )
        return out, meta

    def generate_lessons(
        self,
        *,
        evidence_context: dict[str, Any],
        attribution: AttributionAnalysisOut,
        discrepancy: dict[str, Any],
    ) -> tuple[CandidateLessonsOut, dict[str, Any]]:
        allowed = _ids(evidence_context)
        meta = {
            "model_id": self._model_id,
            "prompt_version": PROMPT_VERSION_LESSONS,
            "prompt_hash": _prompt_hash(
                PROMPT_VERSION_LESSONS,
                {"metric": discrepancy.get("metric_name"), "n": len(attribution.hypotheses)},
            ),
            "provider": "mock",
        }

        if attribution.abstain or not attribution.hypotheses:
            return (
                CandidateLessonsOut(
                    lessons=[],
                    abstain=True,
                    abstain_reason=attribution.abstain_reason
                    or "No hypotheses available for lesson generation",
                ),
                meta,
            )

        lessons: list[CandidateLessonOut] = []
        mission_type = evidence_context.get("mission_type")
        platform = evidence_context.get("platform_type")

        for hypo in attribution.hypotheses:
            if hypo.claims_causation:
                continue  # mock never emits these; safety filter
            support_ids = [
                c.evidence_id
                for c in hypo.supporting_evidence
                if c.evidence_id in allowed
            ]
            if not support_ids and hypo.missing_evidence:
                # Insufficient grounded support → skip lesson
                continue
            lessons.append(
                CandidateLessonOut(
                    claim=hypo.statement,
                    applicability={
                        "mission_type": mission_type,
                        "platform_type": platform,
                        "metric": discrepancy.get("metric_name"),
                    },
                    supporting_evidence_ids=support_ids,
                    contradictory_evidence_ids=[
                        c.evidence_id
                        for c in hypo.contradictory_evidence
                        if c.evidence_id in allowed
                    ],
                    limitations=(
                        "Hypothesis only; not an approved belief. "
                        "Correlation must not be treated as causation."
                    ),
                    uncertainty=hypo.uncertainty,
                    safety_impact="operational",
                    is_hypothesis_only=True,
                    model_confidence_uncalibrated=hypo.model_confidence_uncalibrated,
                    evidence_support_label=hypo.evidence_support_label,
                )
            )

        if not lessons:
            return (
                CandidateLessonsOut(
                    lessons=[],
                    abstain=True,
                    abstain_reason="Insufficient grounded evidence for candidate lessons",
                ),
                meta,
            )

        return CandidateLessonsOut(lessons=lessons, abstain=False), meta
