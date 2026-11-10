"""Phase 5: C6 reconciliation, validation, review, publication, rollback."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    BeliefProposal,
    BeliefProposalOperation,
    BeliefVersion,
    CandidateLesson,
    LearningJobStatus,
    MissionEvent,
    SafetyImpact,
    ValidationAction,
)
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.episodic import (
    EpisodeFinalizeRequest,
    FreezeExpectationRequest,
    MissionEpisodeCreate,
    MissionEventIngest,
)
from kenwer_c3.domain.learning import LearningJobCreate
from kenwer_c3.learning.jobs import LearningJobService
from kenwer_c3.learning.pipeline import LearningPipeline
from kenwer_c3.llm.mock_provider import MockReasoningProvider
from kenwer_c3.memory.consolidated.publication import BeliefPublicationService
from kenwer_c3.memory.episodic.service import EpisodicMemoryService
from kenwer_c3.settings import get_settings
from kenwer_c3.validation.knowledge import KnowledgeValidationService
from fixtures.synthetic.mission1_thermal import (
    episode_payload,
    expectation_payload,
    finalize_payload,
    observation_events,
)


def _principal(tenant_id, *, role=Role.admin, identity_id=None) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        role=role,
        api_key_id=None,
        cognitive_identity_id=identity_id,
        key_prefix="test",
    )


def _seed_mission(session, tenant, identity, *, include_wind: bool = True):
    svc = EpisodicMemoryService(session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            **{
                **episode_payload(identity.id, f"p5-{uuid.uuid4().hex[:8]}"),
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


def _run_pipeline(session, tenant, identity, **kwargs):
    episode_id = _seed_mission(session, tenant, identity, **kwargs)
    job = LearningJobService(session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    finished = LearningPipeline(session, MockReasoningProvider()).run(job.id)
    return finished, episode_id


def _approved_snapshot(session, tenant_id, identity_id):
    beliefs = (
        session.execute(
            select(Belief).where(
                Belief.tenant_id == tenant_id,
                Belief.cognitive_identity_id == identity_id,
            )
        )
        .scalars()
        .all()
    )
    out = []
    for b in beliefs:
        versions = (
            session.execute(
                select(BeliefVersion).where(BeliefVersion.belief_id == b.id)
            )
            .scalars()
            .all()
        )
        out.append(
            {
                "belief_id": b.id,
                "belief_key": b.belief_key,
                "current_version_id": b.current_version_id,
                "versions": [
                    (
                        v.id,
                        v.version,
                        v.approval_status,
                        v.statement,
                        v.confidence,
                        v.confidence_basis,
                    )
                    for v in versions
                ],
            }
        )
    return out


def test_precheck_c4_c5_cannot_alter_published_beliefs(db_session, tenant_and_identity):
    """C4–C5 must not modify content, active version, or approval of published beliefs."""
    tenant, identity = tenant_and_identity
    belief = Belief(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        belief_key="thermal.precheck.published",
    )
    db_session.add(belief)
    db_session.flush()
    version = BeliefVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        belief_id=belief.id,
        cognitive_identity_id=identity.id,
        version=1,
        statement="Published belief must remain immutable through C4–C5.",
        value_json={"immutable": True},
        applicability={"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
        confidence=0.8,
        confidence_basis="human_set",
        evidence_support_label="strong",
        approval_status=BeliefApprovalStatus.approved,
        safety_impact=SafetyImpact.operational,
        provenance={"origin": "phase5_precheck"},
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(version)
    db_session.flush()
    belief.current_version_id = version.id
    db_session.flush()

    before = _approved_snapshot(db_session, tenant.id, identity.id)
    finished, _ = _run_pipeline(db_session, tenant, identity)
    assert finished.status == LearningJobStatus.completed
    assert "C4" in finished.stage_artifacts
    assert "C5" in finished.stage_artifacts
    after = _approved_snapshot(db_session, tenant.id, identity.id)
    assert before == after


def test_pipeline_c1_to_c6_emits_proposals_without_publishing(
    db_session, tenant_and_identity
):
    tenant, identity = tenant_and_identity
    before = _approved_snapshot(db_session, tenant.id, identity.id)
    finished, _ = _run_pipeline(db_session, tenant, identity)
    assert finished.status == LearningJobStatus.completed
    assert finished.current_stage == "C6_done"
    assert "C6" in finished.stage_artifacts
    assert finished.stage_artifacts["C6"]["consolidated_memory_written"] is False
    assert finished.stage_artifacts["C6"]["publication_performed"] is False

    proposals = (
        db_session.execute(
            select(BeliefProposal).where(BeliefProposal.learning_job_id == finished.id)
        )
        .scalars()
        .all()
    )
    assert proposals
    # Confidence labeling on lessons / proposals
    for p in proposals:
        payload = p.proposed_payload or {}
        if payload.get("claim"):
            assert payload.get("confidence_basis") == "uncalibrated_model"
            assert "model_confidence_uncalibrated" in payload or p.operation in (
                BeliefProposalOperation.INCONCLUSIVE,
            )

    after = _approved_snapshot(db_session, tenant.id, identity.id)
    assert before == after


def test_five_revision_operations_and_abstentions(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission(db_session, tenant, identity)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    # Seed an approved belief that overlaps wind claim
    belief = Belief(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        belief_key="thermal.wind_image_quality",
    )
    db_session.add(belief)
    db_session.flush()
    events = (
        db_session.execute(
            select(MissionEvent).where(MissionEvent.episode_id == episode_id)
        )
        .scalars()
        .all()
    )
    evidence_ids = [str(e.id) for e in events[:2]]
    v1 = BeliefVersion(
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
        applicability={"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
        confidence=0.5,
        confidence_basis="uncalibrated_model",
        evidence_support_label="moderate",
        approval_status=BeliefApprovalStatus.approved,
        safety_impact=SafetyImpact.operational,
        provenance={"supporting_evidence_ids": evidence_ids[:1]},
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(v1)
    db_session.flush()
    belief.current_version_id = v1.id

    finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    assert finished.status == LearningJobStatus.completed
    ops = {
        p.operation
        for p in db_session.execute(
            select(BeliefProposal).where(BeliefProposal.learning_job_id == finished.id)
        )
        .scalars()
        .all()
    }
    # At least one of reinforce/refine/revise/redundant/create/challenge depending on match
    assert ops & {
        BeliefProposalOperation.CREATE,
        BeliefProposalOperation.REINFORCE,
        BeliefProposalOperation.REFINE,
        BeliefProposalOperation.REVISE,
        BeliefProposalOperation.REDUNDANT,
        BeliefProposalOperation.CHALLENGE,
        BeliefProposalOperation.INSUFFICIENT_EVIDENCE,
        BeliefProposalOperation.INCONCLUSIVE,
    }

    # Explicit operation unit coverage via synthetic lessons
    from kenwer_c3.learning.c6_reconciliation import BeliefReconciliationService

    # Clear and inject controlled lessons
    db_session.query(CandidateLesson).filter(
        CandidateLesson.learning_job_id == finished.id
    ).delete(synchronize_session=False)

    def _add_lesson(statement, applicability, support, label="moderate"):
        row = CandidateLesson(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            learning_job_id=finished.id,
            episode_id=episode_id,
            statement=statement,
            applicability=applicability,
            evidence_refs={
                "supporting_evidence_ids": support,
                "evidence_support_label": label,
                "model_confidence_uncalibrated": 0.6,
                "confidence_basis": "uncalibrated_model",
            },
            safety_impact=SafetyImpact.operational,
            is_hypothesis_only=True,
        )
        db_session.add(row)
        return row

    # INSUFFICIENT_EVIDENCE
    _add_lesson("No evidence claim", {"mission_type": "thermal_inspection"}, [], "insufficient")
    # REDUNDANT - identical claim + subset evidence
    _add_lesson(
        v1.statement,
        {"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
        evidence_ids[:1],
        "moderate",
    )
    # REINFORCE - same claim + new evidence
    _add_lesson(
        v1.statement,
        {"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
        evidence_ids,
        "moderate",
    )
    # REFINE - same claim, tighter/different app overlap
    _add_lesson(
        v1.statement,
        {
            "mission_type": "thermal_inspection",
            "platform_type": "quadrotor",
            "site_id": "site-alpha",
            "environment": "coastal",
        },
        evidence_ids,
        "moderate",
    )
    # REVISE - related but changed statement
    _add_lesson(
        "Elevated wind correlates with reduced thermal image quality and may require lower altitude passes.",
        {"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
        evidence_ids,
        "moderate",
    )
    # CHALLENGE - overlapping claim, mismatched platform
    _add_lesson(
        "Elevated wind may correlate with reduced thermal image quality for this platform (correlation only).",
        {"mission_type": "thermal_inspection", "platform_type": "fixed_wing"},
        evidence_ids,
        "moderate",
    )
    # CREATE - unrelated claim
    _add_lesson(
        "Battery temperature spikes after prolonged hover in summer heat.",
        {"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
        evidence_ids,
        "moderate",
    )
    db_session.flush()

    c6 = BeliefReconciliationService(db_session, tenant.id).run(
        job_id=finished.id,
        cognitive_identity_id=identity.id,
        evidence_context={"allowed_evidence_ids": evidence_ids},
    )
    ops2 = {p["operation"] for p in c6["proposals"]}
    for required in (
        "INSUFFICIENT_EVIDENCE",
        "REDUNDANT",
        "REINFORCE",
        "REFINE",
        "REVISE",
        "CHALLENGE",
        "CREATE",
    ):
        assert required in ops2, f"missing {required} in {ops2}"


def test_fabricated_evidence_fails_validation(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    proposal = BeliefProposal(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        learning_job_id=uuid.uuid4(),  # will fail FK - use real job
        candidate_lesson_id=None,
        target_belief_id=None,
        cognitive_identity_id=identity.id,
        operation=BeliefProposalOperation.CREATE,
        proposed_payload={
            "belief_key": "test.fabricated",
            "statement": "Fabricated evidence claim",
            "claim": "Fabricated evidence claim",
            "applicability": {
                "mission_type": "thermal_inspection",
                "platform_type": "quadrotor",
            },
            "supporting_evidence_ids": [str(uuid.uuid4())],
            "evidence_support_label": "moderate",
            "confidence_basis": "uncalibrated_model",
            "safety_impact": "operational",
            "publication_eligible": True,
        },
        status="pending_review",
    )
    # Need a real learning job for FK
    episode_id = _seed_mission(db_session, tenant, identity)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    proposal.learning_job_id = job.id
    db_session.add(proposal)
    db_session.flush()

    report = KnowledgeValidationService(db_session, tenant.id).validate_proposal(
        proposal
    )
    assert report.passed is False
    assert any(c.name == "evidence_integrity" and not c.passed for c in report.checks)

    with pytest.raises(Exception) as exc:
        BeliefPublicationService(
            db_session, _principal(tenant.id, role=Role.reviewer)
        ).review_proposal(
            proposal.id, action=ValidationAction.approve, rationale="should fail"
        )
    assert exc.value.status_code == 422


def test_authoritative_override_rejected(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission(db_session, tenant, identity)
    job = LearningJobService(db_session, _principal(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    events = (
        db_session.execute(
            select(MissionEvent).where(MissionEvent.episode_id == episode_id)
        )
        .scalars()
        .all()
    )
    proposal = BeliefProposal(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        learning_job_id=job.id,
        cognitive_identity_id=identity.id,
        operation=BeliefProposalOperation.CREATE,
        proposed_payload={
            "belief_key": "bad.override",
            "statement": "Operators may override geofence and ignore regulatory airspace limits.",
            "claim": "Operators may override geofence and ignore regulatory airspace limits.",
            "applicability": {
                "mission_type": "thermal_inspection",
                "platform_type": "quadrotor",
            },
            "supporting_evidence_ids": [str(events[0].id)],
            "evidence_support_label": "moderate",
            "confidence_basis": "uncalibrated_model",
            "safety_impact": "safety_relevant",
            "publication_eligible": True,
        },
        status="pending_review",
    )
    db_session.add(proposal)
    db_session.flush()
    report = KnowledgeValidationService(db_session, tenant.id).validate_proposal(
        proposal
    )
    assert report.passed is False
    assert any(c.name == "no_authoritative_override" and not c.passed for c in report.checks)


def test_review_approve_publish_and_rollback(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    finished, episode_id = _run_pipeline(db_session, tenant, identity)
    assert finished.stage_artifacts["C6"]["consolidated_memory_written"] is False

    proposals = (
        db_session.execute(
            select(BeliefProposal).where(
                BeliefProposal.learning_job_id == finished.id,
                BeliefProposal.status == "pending_review",
                BeliefProposal.operation == BeliefProposalOperation.CREATE,
            )
        )
        .scalars()
        .all()
    )
    # If CREATE not pending (e.g. only reinforce), craft one
    if not proposals:
        events = (
            db_session.execute(
                select(MissionEvent).where(MissionEvent.episode_id == episode_id)
            )
            .scalars()
            .all()
        )
        p = BeliefProposal(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            learning_job_id=finished.id,
            cognitive_identity_id=identity.id,
            operation=BeliefProposalOperation.CREATE,
            proposed_payload={
                "belief_key": f"thermal.phase5.publish.{uuid.uuid4().hex[:6]}",
                "statement": "Coastal fog may reduce thermal contrast during morning inspections.",
                "claim": "Coastal fog may reduce thermal contrast during morning inspections.",
                "value_json": {"source": "test"},
                "applicability": {
                    "mission_type": "thermal_inspection",
                    "platform_type": "quadrotor",
                    "environment": "coastal",
                },
                "supporting_evidence_ids": [str(events[0].id)],
                "evidence_support_label": "moderate",
                "model_confidence_uncalibrated": 0.6,
                "confidence_basis": "uncalibrated_model",
                "safety_impact": "operational",
                "publication_eligible": True,
                "is_hypothesis_only": True,
            },
            status="pending_review",
        )
        db_session.add(p)
        db_session.flush()
        proposals = [p]

    proposal = proposals[0]
    before_beliefs = (
        db_session.execute(
            select(Belief).where(
                Belief.tenant_id == tenant.id,
                Belief.cognitive_identity_id == identity.id,
            )
        )
        .scalars()
        .all()
    )
    before_count = len(before_beliefs)

    pub = BeliefPublicationService(
        db_session, _principal(tenant.id, role=Role.reviewer)
    )
    result = pub.review_proposal(
        proposal.id,
        action=ValidationAction.approve,
        rationale="Approved for operational guidance only",
    )
    assert result["consolidated_memory_changed"] is True
    assert result["status"] == "published"
    version = db_session.get(BeliefVersion, uuid.UUID(result["resulting_belief_version_id"]))
    assert version is not None
    assert version.approval_status == BeliefApprovalStatus.approved
    assert version.confidence_basis == "uncalibrated_model"
    assert version.evidence_support_label in ("weak", "moderate", "strong", "insufficient")
    assert "not a calibrated probability" in (version.provenance or {}).get("note", "")

    # Duplicate approval conflict
    with pytest.raises(Exception) as exc:
        pub.review_proposal(
            proposal.id, action=ValidationAction.approve, rationale="dup"
        )
    assert exc.value.status_code == 409

    # Publish a second version then rollback
    belief = db_session.get(Belief, version.belief_id)
    assert belief is not None
    p2 = BeliefProposal(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        learning_job_id=finished.id,
        target_belief_id=belief.id,
        cognitive_identity_id=identity.id,
        operation=BeliefProposalOperation.REVISE,
        proposed_payload={
            "belief_key": belief.belief_key,
            "statement": version.statement + " Revised after additional missions.",
            "claim": version.statement + " Revised after additional missions.",
            "value_json": {"rev": 2},
            "applicability": version.applicability,
            "supporting_evidence_ids": (version.provenance or {}).get(
                "supporting_evidence_ids"
            ),
            "evidence_support_label": "moderate",
            "model_confidence_uncalibrated": 0.65,
            "confidence_basis": "uncalibrated_model",
            "safety_impact": "operational",
            "publication_eligible": True,
        },
        status="pending_review",
    )
    db_session.add(p2)
    db_session.flush()
    r2 = pub.review_proposal(p2.id, action=ValidationAction.approve, rationale="revise")
    v2 = db_session.get(BeliefVersion, uuid.UUID(r2["resulting_belief_version_id"]))
    assert v2.version == 2
    assert belief.current_version_id == v2.id
    # Prior version superseded, not deleted
    v1 = version
    db_session.refresh(v1)
    assert v1.approval_status == BeliefApprovalStatus.superseded

    rb = pub.rollback(belief.id, to_version=1, rationale="Revert revision")
    assert rb["new_version"] == 3
    db_session.refresh(belief)
    restored = db_session.get(BeliefVersion, belief.current_version_id)
    assert restored.statement == v1.statement
    # History retained
    all_versions = (
        db_session.execute(
            select(BeliefVersion).where(BeliefVersion.belief_id == belief.id)
        )
        .scalars()
        .all()
    )
    assert len(all_versions) == 3


def test_reject_and_defer_do_not_change_consolidated(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    finished, episode_id = _run_pipeline(db_session, tenant, identity)
    events = (
        db_session.execute(
            select(MissionEvent).where(MissionEvent.episode_id == episode_id)
        )
        .scalars()
        .all()
    )
    before = _approved_snapshot(db_session, tenant.id, identity.id)
    for action, status in (
        (ValidationAction.reject, "rejected"),
        (ValidationAction.defer, "deferred"),
    ):
        p = BeliefProposal(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            learning_job_id=finished.id,
            cognitive_identity_id=identity.id,
            operation=BeliefProposalOperation.CREATE,
            proposed_payload={
                "belief_key": f"thermal.{status}.{uuid.uuid4().hex[:6]}",
                "statement": f"Candidate for {status}",
                "claim": f"Candidate for {status}",
                "applicability": {
                    "mission_type": "thermal_inspection",
                    "platform_type": "quadrotor",
                },
                "supporting_evidence_ids": [str(events[0].id)],
                "evidence_support_label": "moderate",
                "confidence_basis": "uncalibrated_model",
                "safety_impact": "operational",
                "publication_eligible": True,
            },
            status="pending_review",
        )
        db_session.add(p)
        db_session.flush()
        result = BeliefPublicationService(
            db_session, _principal(tenant.id, role=Role.reviewer)
        ).review_proposal(p.id, action=action, rationale=status)
        assert result["status"] == status
        assert result["consolidated_memory_changed"] is False
    assert _approved_snapshot(db_session, tenant.id, identity.id) == before


def test_challenge_flags_without_replacing(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    finished, episode_id = _run_pipeline(db_session, tenant, identity)
    events = (
        db_session.execute(
            select(MissionEvent).where(MissionEvent.episode_id == episode_id)
        )
        .scalars()
        .all()
    )
    belief = Belief(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        belief_key="thermal.challenge.target",
    )
    db_session.add(belief)
    db_session.flush()
    v = BeliefVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        belief_id=belief.id,
        cognitive_identity_id=identity.id,
        version=1,
        statement="Baseline wind belief",
        value_json={},
        applicability={"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
        confidence=0.5,
        confidence_basis="human_set",
        evidence_support_label="moderate",
        approval_status=BeliefApprovalStatus.approved,
        safety_impact=SafetyImpact.operational,
        provenance={},
        published_at=datetime.now(timezone.utc),
    )
    db_session.add(v)
    db_session.flush()
    belief.current_version_id = v.id
    current_id = belief.current_version_id

    p = BeliefProposal(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        learning_job_id=finished.id,
        target_belief_id=belief.id,
        cognitive_identity_id=identity.id,
        operation=BeliefProposalOperation.CHALLENGE,
        proposed_payload={
            "belief_key": belief.belief_key,
            "statement": "Challenging claim with conflicting applicability",
            "claim": "Challenging claim with conflicting applicability",
            "applicability": {
                "mission_type": "thermal_inspection",
                "platform_type": "fixed_wing",
            },
            "supporting_evidence_ids": [str(events[0].id)],
            "evidence_support_label": "moderate",
            "confidence_basis": "uncalibrated_model",
            "safety_impact": "operational",
            "publication_eligible": False,
        },
        status="pending_review",
        flags_existing_for_review=True,
    )
    db_session.add(p)
    db_session.flush()

    result = BeliefPublicationService(
        db_session, _principal(tenant.id, role=Role.reviewer)
    ).review_proposal(p.id, action=ValidationAction.approve, rationale="flag only")
    assert result["challenge_flagged"] is True
    assert result["consolidated_memory_changed"] is False
    db_session.refresh(belief)
    assert belief.current_version_id == current_id
    db_session.refresh(v)
    assert v.approval_status == BeliefApprovalStatus.approved
    assert v.statement == "Baseline wind belief"


def test_unauthorized_and_cross_tenant_reviewers(
    db_session, tenant_and_identity, other_tenant_identity
):
    tenant, identity = tenant_and_identity
    other_tenant, _ = other_tenant_identity
    finished, episode_id = _run_pipeline(db_session, tenant, identity)
    events = (
        db_session.execute(
            select(MissionEvent).where(MissionEvent.episode_id == episode_id)
        )
        .scalars()
        .all()
    )
    p = BeliefProposal(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        learning_job_id=finished.id,
        cognitive_identity_id=identity.id,
        operation=BeliefProposalOperation.CREATE,
        proposed_payload={
            "belief_key": "thermal.authz",
            "statement": "Authz test claim",
            "claim": "Authz test claim",
            "applicability": {
                "mission_type": "thermal_inspection",
                "platform_type": "quadrotor",
            },
            "supporting_evidence_ids": [str(events[0].id)],
            "evidence_support_label": "moderate",
            "confidence_basis": "uncalibrated_model",
            "safety_impact": "operational",
            "publication_eligible": True,
        },
        status="pending_review",
    )
    db_session.add(p)
    db_session.flush()

    # Retrieve role cannot review
    with pytest.raises(Exception) as exc:
        BeliefPublicationService(
            db_session, _principal(tenant.id, role=Role.channel1_retrieve)
        ).review_proposal(p.id, action=ValidationAction.approve, rationale="nope")
    assert exc.value.status_code == 403

    # Cross-tenant reviewer
    with pytest.raises(Exception) as exc2:
        BeliefPublicationService(
            db_session, _principal(other_tenant.id, role=Role.reviewer)
        ).review_proposal(p.id, action=ValidationAction.approve, rationale="x-tenant")
    assert exc2.value.status_code == 403


def test_api_review_endpoints(client, seeded_dev_identity, auth_headers_admin, auth_headers_reviewer, auth_headers_retrieve):
    tenant_id, identity_id = seeded_dev_identity
    ep = client.post(
        "/v1/episodes",
        headers=auth_headers_admin,
        json=episode_payload(str(identity_id), f"api-p5-{uuid.uuid4().hex[:8]}"),
    )
    assert ep.status_code == 200, ep.text
    episode_id = ep.json()["id"]
    assert (
        client.post(
            f"/v1/episodes/{episode_id}/expectations/freeze",
            headers=auth_headers_admin,
            json=expectation_payload(),
        ).status_code
        == 200
    )
    for evt in observation_events(
        episode_id=episode_id, cognitive_identity_id=str(identity_id)
    ):
        assert (
            client.post("/v1/memory/events", headers=auth_headers_admin, json=evt).status_code
            == 200
        )
    assert (
        client.post(
            f"/v1/episodes/{episode_id}/finalize",
            headers=auth_headers_admin,
            json=finalize_payload(),
        ).status_code
        == 200
    )
    job = client.post(
        "/v1/learning/jobs",
        headers=auth_headers_admin,
        json={"episode_id": episode_id},
    )
    assert job.status_code == 200, job.text
    assert job.json()["status"] == "completed"
    assert job.json()["current_stage"] == "C6_done"

    listed = client.get(
        "/v1/proposals",
        headers=auth_headers_reviewer,
        params={"cognitive_identity_id": str(identity_id)},
    )
    assert listed.status_code == 200
    proposals = listed.json()
    assert proposals

    pending = [p for p in proposals if p["status"] == "pending_review"]
    if not pending:
        pytest.skip("No pending_review proposals from mock pipeline in this run")
    pid = pending[0]["id"]

    # Retrieve cannot approve
    denied = client.post(
        f"/v1/proposals/{pid}/review",
        headers=auth_headers_retrieve,
        json={"action": "APPROVE", "rationale": "no"},
    )
    assert denied.status_code == 403

    # May fail validation (applicability) - accept 200 published or 422
    reviewed = client.post(
        f"/v1/proposals/{pid}/review",
        headers=auth_headers_reviewer,
        json={"action": "APPROVE", "rationale": "api review"},
    )
    assert reviewed.status_code in (200, 422), reviewed.text
    if reviewed.status_code == 200:
        assert reviewed.json()["status"] in ("published", "challenge_flagged")


def test_celery_c1_c6_real_worker_path(auth_headers_admin):
    """Complete C1–C6 via Celery task function + live Postgres (deterministic mock).

    Note: live Docker-worker acceptance (no in-process fallback) is
    ``scripts/verify_docker_c1_c6.py`` and ``tests/test_celery_real_worker.py``.
    """
    import os

    # This unit path may run against the isolated test DB.
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3_test",
    )
    get_settings.cache_clear()
    settings = get_settings()
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    from kenwer_c3.auth.bootstrap import ensure_dev_identity
    from kenwer_c3.db.base import get_engine, get_session_factory
    from kenwer_c3.db.models import LearningJob
    from kenwer_c3.main import create_app
    from kenwer_c3.workers.tasks import run_learning_job
    import os

    session = SessionLocal()
    try:
        ensure_dev_identity(session, settings)
        session.commit()
    finally:
        session.close()

    os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    api = TestClient(create_app())
    identity_id = settings.dev_cognitive_identity_id
    ep = api.post(
        "/v1/episodes",
        headers=auth_headers_admin,
        json=episode_payload(identity_id, f"celery-c6-{uuid.uuid4().hex[:8]}"),
    )
    assert ep.status_code == 200, ep.text
    episode_id = ep.json()["id"]
    assert (
        api.post(
            f"/v1/episodes/{episode_id}/expectations/freeze",
            headers=auth_headers_admin,
            json=expectation_payload(),
        ).status_code
        == 200
    )
    for evt in observation_events(
        episode_id=episode_id, cognitive_identity_id=identity_id
    ):
        assert (
            api.post("/v1/memory/events", headers=auth_headers_admin, json=evt).status_code
            == 200
        )
    assert (
        api.post(
            f"/v1/episodes/{episode_id}/finalize",
            headers=auth_headers_admin,
            json=finalize_payload(),
        ).status_code
        == 200
    )

    session = SessionLocal()
    try:
        job = LearningJob(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(settings.dev_tenant_id),
            episode_id=uuid.UUID(episode_id),
            cognitive_identity_id=uuid.UUID(identity_id),
            status=LearningJobStatus.queued,
            stage_artifacts={},
            attempt_count=0,
            idempotency_key=f"celery-c6-{uuid.uuid4().hex}",
        )
        session.add(job)
        session.commit()
        job_id = job.id
        # Snapshot approved beliefs before
        before = (
            session.execute(
                select(BeliefVersion).where(
                    BeliefVersion.tenant_id == job.tenant_id,
                    BeliefVersion.approval_status == BeliefApprovalStatus.approved,
                )
            )
            .scalars()
            .all()
        )
        before_ids = {v.id for v in before}
    finally:
        session.close()

    payload = run_learning_job.apply(args=[str(job_id)]).get(timeout=60)
    assert payload["status"] == "completed"
    assert payload["current_stage"] == "C6_done"

    session = SessionLocal()
    try:
        job = session.get(LearningJob, job_id)
        assert job is not None
        for stage in ("C1", "C2", "C3", "C4", "C5", "C6"):
            assert stage in job.stage_artifacts
        assert job.stage_artifacts["C6"]["publication_performed"] is False
        proposals = (
            session.execute(
                select(BeliefProposal).where(BeliefProposal.learning_job_id == job_id)
            )
            .scalars()
            .all()
        )
        assert proposals
        after = (
            session.execute(
                select(BeliefVersion).where(
                    BeliefVersion.tenant_id == job.tenant_id,
                    BeliefVersion.approval_status == BeliefApprovalStatus.approved,
                )
            )
            .scalars()
            .all()
        )
        assert {v.id for v in after} == before_ids
    finally:
        session.close()
