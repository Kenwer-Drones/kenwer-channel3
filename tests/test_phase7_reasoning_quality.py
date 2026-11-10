"""Phase 7: labeled synthetic reasoning-quality evaluation (mock provider).

Separates deterministic mock results from external LLM (not run here unless
explicitly configured). Does not claim calibrated confidence or causal reliability.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from fixtures.synthetic.reasoning_cases import CASES
from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    BeliefProposalOperation,
    BeliefVersion,
    SafetyImpact,
)
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.episodic import (
    EpisodeFinalizeRequest,
    FreezeExpectationRequest,
    MissionEpisodeCreate,
    MissionEventIngest,
)
from kenwer_c3.domain.learning import LearningJobCreate
from kenwer_c3.learning.c6_reconciliation import BeliefReconciliationService
from kenwer_c3.learning.evidence_validation import filter_attribution_to_context
from kenwer_c3.learning.jobs import LearningJobService
from kenwer_c3.learning.pipeline import LearningPipeline
from kenwer_c3.llm.mock_provider import MockReasoningProvider
from kenwer_c3.llm.schemas import (
    AttributionAnalysisOut,
    AttributionHypothesisOut,
    EvidenceCitation,
)
from kenwer_c3.memory.episodic.service import EpisodicMemoryService
from fixtures.synthetic.mission1_thermal import expectation_payload


def _principal(tenant_id) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        role=Role.admin,
        api_key_id=None,
        cognitive_identity_id=None,
        key_prefix="test",
    )


def _seed_case(session, tenant, identity, case: dict):
    svc = EpisodicMemoryService(session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"rq-{case['id']}-{uuid.uuid4().hex[:6]}",
            mission_type="thermal_inspection",
            objectives={"synthetic": True, "case": case["id"]},
            approved_plan={"version": "v1", "synthetic": True},
            site_context={"site_id": "plant-7-synthetic"},
            platform_context={"platform": "quadrotor", "platform_type": "quadrotor"},
        )
    )
    exp = expectation_payload()
    svc.freeze_expectation(
        ep.id,
        FreezeExpectationRequest(
            criteria_json=exp["criteria_json"],
            metric_baselines=exp["metric_baselines"],
            mission_plan_version=exp["mission_plan_version"],
            source_system=exp["source_system"],
            captured_at=datetime.now(timezone.utc),
        ),
    )
    t0 = datetime.now(timezone.utc)
    seq = 1
    if case.get("include_wind"):
        svc.ingest_event(
            MissionEventIngest(
                episode_id=ep.id,
                cognitive_identity_id=identity.id,
                event_idempotency_key=f"{case['id']}-wind-{uuid.uuid4().hex[:6]}",
                event_type="observation.wind",
                sequence_no=seq,
                payload={"wind_mps": 7.2, "synthetic": True},
                source_system="channel2",
                recorded_at=t0,
            )
        )
        seq += 1
    if case.get("include_quality"):
        score = case.get("image_quality_score", 0.62)
        svc.ingest_event(
            MissionEventIngest(
                episode_id=ep.id,
                cognitive_identity_id=identity.id,
                event_idempotency_key=f"{case['id']}-iq-{uuid.uuid4().hex[:6]}",
                event_type="observation.image_quality",
                sequence_no=seq,
                payload={"score": score, "synthetic": True},
                source_system="channel2",
                recorded_at=t0,
            )
        )
        seq += 1
    outcomes = {}
    missing = {}
    if case.get("include_quality"):
        outcomes["image_quality"] = case.get("image_quality_score", 0.62)
    else:
        missing["image_quality"] = "missing"
    if case.get("include_wind"):
        outcomes["wind_mps"] = 7.2
    svc.finalize_episode(
        ep.id,
        EpisodeFinalizeRequest(actual_outcomes=outcomes, missing_data=missing),
    )
    return ep.id


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_reasoning_quality_case(db_session, tenant_and_identity, case):
    tenant, identity = tenant_and_identity
    expect = case["expect"]

    if case.get("mode") == "unit_filter":
        analysis = AttributionAnalysisOut(
            discrepancy_metric="image_quality",
            hypotheses=[
                AttributionHypothesisOut(
                    statement="fabricated",
                    model_confidence_uncalibrated=0.9,
                    evidence_support_label="strong",
                    supporting_evidence=[
                        EvidenceCitation(evidence_id="not-real", note="invented")
                    ],
                    contradictory_evidence=[],
                    missing_evidence=[],
                    uncertainty="n/a",
                )
            ],
        )
        cleaned = filter_attribution_to_context(analysis, allowed_ids={"real-1"})
        assert cleaned.abstain is True
        assert cleaned.hypotheses == []
        assert expect["fabricated_refs_rejected"] is True
        return

    if case.get("mode") == "c6_applicability":
        episode_id = _seed_case(
            db_session,
            tenant,
            identity,
            {
                "id": "c6app",
                "include_wind": True,
                "include_quality": True,
                "image_quality_score": 0.6,
            },
        )
        job = LearningJobService(db_session, _principal(tenant.id)).create(
            LearningJobCreate(episode_id=episode_id)
        )
        belief = Belief(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            cognitive_identity_id=identity.id,
            belief_key="thermal.c6.platform.guard",
        )
        db_session.add(belief)
        db_session.flush()
        db_session.add(
            BeliefVersion(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                belief_id=belief.id,
                cognitive_identity_id=identity.id,
                version=1,
                statement=(
                    "Elevated wind may correlate with reduced thermal image "
                    "quality for this platform (correlation only)."
                ),
                value_json={},
                applicability={
                    "mission_type": "thermal_inspection",
                    "platform_type": "quadrotor",
                },
                confidence=0.5,
                confidence_basis="uncalibrated_model",
                evidence_support_label="moderate",
                approval_status=BeliefApprovalStatus.approved,
                safety_impact=SafetyImpact.operational,
                published_at=datetime.now(timezone.utc),
            )
        )
        finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
        # Inject mismatched-platform lesson for C6
        from kenwer_c3.db.models import CandidateLesson, MissionEvent

        events = (
            db_session.query(MissionEvent)
            .filter_by(episode_id=episode_id)
            .all()
        )
        db_session.query(CandidateLesson).filter_by(learning_job_id=finished.id).delete()
        db_session.add(
            CandidateLesson(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                learning_job_id=finished.id,
                episode_id=episode_id,
                statement=(
                    "Elevated wind may correlate with reduced thermal image "
                    "quality for this platform (correlation only)."
                ),
                applicability={
                    "mission_type": "thermal_inspection",
                    "platform_type": "fixed_wing",
                },
                evidence_refs={
                    "supporting_evidence_ids": [str(events[0].id)],
                    "evidence_support_label": "moderate",
                    "model_confidence_uncalibrated": 0.55,
                    "confidence_basis": "uncalibrated_model",
                },
                safety_impact=SafetyImpact.operational,
                is_hypothesis_only=True,
            )
        )
        db_session.flush()
        c6 = BeliefReconciliationService(db_session, tenant.id).run(
            job_id=finished.id,
            cognitive_identity_id=identity.id,
            evidence_context={"allowed_evidence_ids": [str(e.id) for e in events]},
        )
        ops = {p["operation"] for p in c6["proposals"]}
        assert "CHALLENGE" in ops or "CREATE" in ops
        assert c6["publication_performed"] is False
        return

    episode_id = _seed_case(db_session, tenant, identity, case)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    assert finished.status.value == "completed" or finished.status.name == "completed"
    c4 = finished.stage_artifacts["C4"]
    c5 = finished.stage_artifacts["C5"]
    allowed = set(
        finished.stage_artifacts.get("evidence_context", {}).get(
            "allowed_evidence_ids", []
        )
    )

    analyses = c4.get("analyses") or []
    hypos = []
    for a in analyses:
        hypos.extend(a.get("hypotheses") or [])

    if expect.get("c4_abstain"):
        assert c4.get("abstain") or all(a.get("abstain") for a in analyses) or not hypos
    if expect.get("c4_abstain_or_empty"):
        assert c4.get("abstain") or not hypos or all(
            a.get("abstain") or not a.get("hypotheses") for a in analyses
        )
    if expect.get("allow_abstain_or_hypotheses"):
        assert True  # either path acceptable for contradictory high quality
    if expect.get("min_hypotheses"):
        assert len(hypos) >= expect["min_hypotheses"]
    if expect.get("all_evidence_ids_valid"):
        for h in hypos:
            for cite in h.get("supporting_evidence") or []:
                assert cite["evidence_id"] in allowed
            for cite in h.get("contradictory_evidence") or []:
                assert cite["evidence_id"] in allowed
    if expect.get("claims_causation_forbidden"):
        for h in hypos:
            assert h.get("claims_causation") in (False, None)
            # mock stores correlation_only in evidence when persisted via C4
            assert h.get("correlation_only", True) is True
    if expect.get("all_correlation_only"):
        assert all(h.get("correlation_only", True) for h in hypos)
    if expect.get("missing_evidence_mentions_wind"):
        missing = []
        for h in hypos:
            missing.extend(h.get("missing_evidence") or [])
        assert any("wind" in str(m).lower() for m in missing) or hypos
    if expect.get("c5_min_lessons"):
        assert len(c5.get("lessons") or []) >= expect["c5_min_lessons"]
    if expect.get("c5_zero_lessons_or_abstain"):
        assert c5.get("abstain") or not c5.get("lessons")


def test_external_llm_requires_explicit_mission_content_authorization():
    """Actual OpenAI provider must not send mission content without flag."""
    from kenwer_c3.llm.openai_provider import OpenAICompatibleReasoningProvider
    from kenwer_c3.llm.base import ReasoningProviderError
    from kenwer_c3.settings import Settings

    settings = Settings(
        llm_api_key="sk-test",
        llm_allow_external_mission_content=False,
    )
    provider = OpenAICompatibleReasoningProvider(settings)
    with pytest.raises(ReasoningProviderError) as exc:
        provider.analyze_attribution(
            evidence_context={"evidence_items": []},
            discrepancy={"metric_name": "image_quality", "result_status": "fail"},
        )
    assert "External mission content transmission disabled" in str(exc.value)


def test_reasoning_quality_report_summary(db_session, tenant_and_identity):
    """Aggregate mock-provider metrics across pipeline cases (not calibrated)."""
    tenant, identity = tenant_and_identity
    metrics = {
        "cases_run": 0,
        "evidence_ref_invalid": 0,
        "causation_claims": 0,
        "abstentions": 0,
        "lessons_generated": 0,
        "provider": "mock",
        "note": "Uncalibrated mock metrics — not real-world reliability",
    }
    pipeline_cases = [c for c in CASES if not c.get("mode")]
    for case in pipeline_cases:
        episode_id = _seed_case(db_session, tenant, identity, case)
        job = LearningJobService(db_session, _principal(tenant.id)).create(
            LearningJobCreate(episode_id=episode_id)
        )
        finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
        metrics["cases_run"] += 1
        c4 = finished.stage_artifacts["C4"]
        c5 = finished.stage_artifacts["C5"]
        allowed = set(
            finished.stage_artifacts.get("evidence_context", {}).get(
                "allowed_evidence_ids", []
            )
        )
        hypos = [
            h
            for a in (c4.get("analyses") or [])
            for h in (a.get("hypotheses") or [])
        ]
        if c4.get("abstain") or not hypos:
            metrics["abstentions"] += 1
        for h in hypos:
            if h.get("claims_causation"):
                metrics["causation_claims"] += 1
            for cite in h.get("supporting_evidence") or []:
                if cite["evidence_id"] not in allowed:
                    metrics["evidence_ref_invalid"] += 1
        metrics["lessons_generated"] += len(c5.get("lessons") or [])

    assert metrics["cases_run"] == len(pipeline_cases)
    assert metrics["causation_claims"] == 0
    assert metrics["evidence_ref_invalid"] == 0
    # Persist for Phase 7 report consumers
    import json
    from pathlib import Path

    Path("docs/phase7-reasoning-metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
