"""Validate model outputs against retrieved evidence - reject nonexistent refs."""

from __future__ import annotations

from kenwer_c3.llm.schemas import AttributionAnalysisOut, CandidateLessonsOut


def filter_attribution_to_context(
    analysis: AttributionAnalysisOut,
    allowed_ids: set[str],
) -> AttributionAnalysisOut:
    """Drop citations to evidence not in context; drop empty/invalid hypotheses."""
    cleaned = []
    for hypo in analysis.hypotheses:
        if hypo.claims_causation:
            # Hard rule: correlation-only pipeline rejects causation claims
            hypo = hypo.model_copy(
                update={"claims_causation": False, "correlation_only": True}
            )
        supporting = [
            c for c in hypo.supporting_evidence if c.evidence_id in allowed_ids
        ]
        contradictory = [
            c for c in hypo.contradictory_evidence if c.evidence_id in allowed_ids
        ]
        # If model invented all supporting refs, treat as invalid unless abstaining
        if hypo.supporting_evidence and not supporting:
            continue
        cleaned.append(
            hypo.model_copy(
                update={
                    "supporting_evidence": supporting,
                    "contradictory_evidence": contradictory,
                }
            )
        )

    if not cleaned and not analysis.abstain:
        return analysis.model_copy(
            update={
                "hypotheses": [],
                "abstain": True,
                "abstain_reason": (
                    analysis.abstain_reason
                    or "All hypotheses rejected: nonexistent or invalid evidence references"
                ),
            }
        )
    return analysis.model_copy(update={"hypotheses": cleaned})


def filter_lessons_to_context(
    lessons: CandidateLessonsOut,
    allowed_ids: set[str],
) -> CandidateLessonsOut:
    cleaned = []
    for lesson in lessons.lessons:
        if not lesson.is_hypothesis_only:
            lesson = lesson.model_copy(update={"is_hypothesis_only": True})
        ids = [i for i in lesson.supporting_evidence_ids if i in allowed_ids]
        invented = [
            i for i in lesson.supporting_evidence_ids if i not in allowed_ids
        ]
        if invented and not ids:
            continue
        cleaned.append(
            lesson.model_copy(update={"supporting_evidence_ids": ids})
        )
    if not cleaned and lessons.lessons and not lessons.abstain:
        return lessons.model_copy(
            update={
                "lessons": [],
                "abstain": True,
                "abstain_reason": (
                    lessons.abstain_reason
                    or "All lessons rejected: nonexistent evidence references"
                ),
            }
        )
    return lessons.model_copy(update={"lessons": cleaned})
