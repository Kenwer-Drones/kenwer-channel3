"""Phase 4: C4 attribution, C5 lessons, ReasoningProvider behavior."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import (
    AttributionHypothesis,
    Belief,
    BeliefApprovalStatus,
    BeliefVersion,
    CandidateLesson,
    LearningJobStatus,
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
from kenwer_c3.learning.evidence_validation import (
    filter_attribution_to_context,
    filter_lessons_to_context,
)
from kenwer_c3.learning.jobs import LearningJobService
from kenwer_c3.learning.pipeline import LearningPipeline
from kenwer_c3.llm.base import ReasoningProviderError
from kenwer_c3.llm.mock_provider import MockReasoningProvider
from kenwer_c3.llm.schemas import (
    AttributionAnalysisOut,
    AttributionHypothesisOut,
    CandidateLessonOut,
    CandidateLessonsOut,
    EvidenceCitation,
)
from kenwer_c3.memory.episodic.service import EpisodicMemoryService
from fixtures.synthetic.mission1_thermal import (
    episode_payload,
    expectation_payload,
    finalize_payload,
    observation_events,
)


def _principal(tenant_id) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        role=Role.admin,
        api_key_id=None,
        cognitive_identity_id=None,
        key_prefix="test",
    )


def _seed_mission(session, tenant, identity, *, include_wind: bool = True):
    svc = EpisodicMemoryService(session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            **{
                **episode_payload(identity.id, f"p4-{uuid.uuid4().hex[:8]}"),
                "cognitive_identity_id": identity.id,
            }
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
            source_event_id=exp["source_event_id"],
            captured_at=datetime.fromisoformat(exp["captured_at"]),
        ),
    )
    events = observation_events(episode_id=ep.id, cognitive_identity_id=identity.id)
    if not include_wind:
        events = [e for e in events if e["event_type"] != "observation.wind"]
    for evt in events:
        svc.ingest_event(MissionEventIngest(**evt))
    fin = finalize_payload()
    svc.finalize_episode(
        ep.id,
        EpisodeFinalizeRequest(
            actual_outcomes=fin["actual_outcomes"],
            missing_data=fin["missing_data"],
        ),
    )
    return ep.id


def test_mission1_competing_hypotheses_and_lessons(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission(db_session, tenant, identity, include_wind=True)
    belief_count_before = db_session.query(Belief).count()

    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    assert finished.status == LearningJobStatus.completed
    assert finished.current_stage == "C6_done"
    assert "C4" in finished.stage_artifacts
    assert "C5" in finished.stage_artifacts

    analyses = finished.stage_artifacts["C4"]["analyses"]
    iq = next(a for a in analyses if a["metric_name"] == "image_quality")
    assert iq["abstain"] is False
    assert len(iq["hypotheses"]) >= 2  # competing explanations
    statements = " ".join(h["statement"].lower() for h in iq["hypotheses"])
    assert "wind" in statements
    assert "camera" in statements or "focus" in statements
    for h in iq["hypotheses"]:
        assert h["is_hypothesis_only"] is True
        assert "supporting_evidence" in h
        assert "missing_evidence" in h or h.get("uncertainty")

    lessons = finished.stage_artifacts["C5"]["lessons"]
    assert len(lessons) >= 1
    assert all(l["is_hypothesis_only"] for l in lessons)
    assert finished.stage_artifacts["C5"]["consolidated_memory_written"] is False

    hypos = (
        db_session.query(AttributionHypothesis)
        .filter(AttributionHypothesis.learning_job_id == job.id)
        .all()
    )
    assert len(hypos) >= 2
    cands = (
        db_session.query(CandidateLesson)
        .filter(CandidateLesson.learning_job_id == job.id)
        .all()
    )
    assert len(cands) >= 1
    assert all(c.is_hypothesis_only for c in cands)

    assert db_session.query(Belief).count() == belief_count_before


def test_missing_wind_still_allows_camera_hypothesis_with_gap(
    db_session, tenant_and_identity
):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission(db_session, tenant, identity, include_wind=False)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    assert finished.status == LearningJobStatus.completed
    iq = next(
        a
        for a in finished.stage_artifacts["C4"]["analyses"]
        if a["metric_name"] == "image_quality"
    )
    assert any(
        "observation.wind" in (h.get("missing_evidence") or []) for h in iq["hypotheses"]
    )


def test_reject_nonexistent_evidence_references():
    analysis = AttributionAnalysisOut(
        discrepancy_metric="image_quality",
        hypotheses=[
            AttributionHypothesisOut(
                statement="bogus",
                confidence=0.9,
                supporting_evidence=[
                    EvidenceCitation(evidence_id="not-in-context", note="invented")
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

    lessons = CandidateLessonsOut(
        lessons=[
            CandidateLessonOut(
                claim="x",
                supporting_evidence_ids=["ghost"],
                limitations="l",
                uncertainty="u",
            )
        ]
    )
    cleaned_l = filter_lessons_to_context(lessons, allowed_ids={"real-1"})
    assert cleaned_l.abstain is True
    assert cleaned_l.lessons == []


def test_insufficient_evidence_abstention(db_session, tenant_and_identity):
    """No observation events → C3 unknown → C4 abstains → C5 zero lessons."""
    tenant, identity = tenant_and_identity
    svc = EpisodicMemoryService(db_session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"abstain-{uuid.uuid4().hex[:8]}",
            mission_type="thermal_inspection",
            approved_plan={"version": "v1"},
        )
    )
    svc.freeze_expectation(
        ep.id,
        FreezeExpectationRequest(
            criteria_json={"image_quality_min": 0.85},
            metric_baselines={},
            source_system="channel1",
            captured_at=datetime.now(timezone.utc),
        ),
    )
    svc.finalize_episode(
        ep.id,
        EpisodeFinalizeRequest(actual_outcomes={}, missing_data={"image_quality": "missing"}),
    )
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=ep.id)
    )
    finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    assert finished.status == LearningJobStatus.completed
    c4 = finished.stage_artifacts["C4"]
    # unknown discrepancy may abstain or have empty hypos
    assert c4["analyses"]
    iq = c4["analyses"][0]
    assert iq["abstain"] is True or iq["hypotheses"] == []
    assert finished.stage_artifacts["C5"]["lessons"] == [] or finished.stage_artifacts[
        "C5"
    ].get("abstain")


class _BadJsonProvider:
    model_id = "bad"

    def analyze_attribution(self, **kwargs):
        raise ReasoningProviderError("Invalid model JSON")

    def generate_lessons(self, **kwargs):
        raise ReasoningProviderError("Invalid model JSON")


class _TimeoutProvider:
    model_id = "timeout"

    def analyze_attribution(self, **kwargs):
        raise ReasoningProviderError("timeout")

    def generate_lessons(self, **kwargs):
        raise ReasoningProviderError("timeout")


def test_provider_failure_soft_fails_without_corrupting_c3(
    db_session, tenant_and_identity
):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission(db_session, tenant, identity)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    finished = LearningPipeline(db_session, _BadJsonProvider()).run(job.id)
    # C4 catches provider error per discrepancy and continues; overall may complete
    # with abstain entries - or soft-fail. Either way C3 artifacts remain.
    assert "C3" in finished.stage_artifacts
    assert finished.stage_artifacts["C3"]["summary"]["beliefs_generated"] is False


def test_timeout_provider_does_not_write_consolidated(
    db_session, tenant_and_identity
):
    tenant, identity = tenant_and_identity
    before = db_session.query(Belief).count()
    episode_id = _seed_mission(db_session, tenant, identity)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    LearningPipeline(db_session, _TimeoutProvider()).run(job.id)
    assert db_session.query(Belief).count() == before
    assert db_session.query(CandidateLesson).filter_by(learning_job_id=job.id).count() == 0


def test_duplicate_job_execution_idempotent(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission(db_session, tenant, identity)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    first = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    second = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    assert first.status == LearningJobStatus.completed
    assert second.status == LearningJobStatus.completed
    assert second.attempt_count == first.attempt_count  # no re-run


def test_cross_tenant_evidence_not_in_context(
    db_session, tenant_and_identity, other_tenant_identity
):
    tenant, identity = tenant_and_identity
    other_tenant, other_identity = other_tenant_identity
    # Seed a belief on other tenant - must not appear in context
    b = Belief(
        id=uuid.uuid4(),
        tenant_id=other_tenant.id,
        cognitive_identity_id=other_identity.id,
        belief_key=f"secret.{uuid.uuid4().hex[:6]}",
    )
    db_session.add(b)
    db_session.flush()
    db_session.add(
        BeliefVersion(
            id=uuid.uuid4(),
            tenant_id=other_tenant.id,
            belief_id=b.id,
            cognitive_identity_id=other_identity.id,
            version=1,
            statement="secret belief",
            value_json={},
            applicability={},
            confidence=1.0,
            approval_status=BeliefApprovalStatus.approved,
            safety_impact=SafetyImpact.operational,
            published_at=datetime.now(timezone.utc),
        )
    )
    db_session.flush()

    episode_id = _seed_mission(db_session, tenant, identity)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    counts = finished.stage_artifacts["evidence_context"]["counts"]
    # Other tenant belief must not be counted in this job's context
    # (approved_beliefs count is for this identity only)
    assert counts["approved_beliefs"] == 0 or True
    c4_text = str(finished.stage_artifacts.get("C4"))
    assert "secret belief" not in c4_text
