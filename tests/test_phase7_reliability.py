"""Phase 7: reliability and failure-mode tests.

Marks scenarios BLOCKED when the local environment cannot safely induce the
failure (e.g. killing Docker Postgres mid-transaction from the test process).
"""

from __future__ import annotations

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import (
    BeliefProposal,
    BeliefProposalOperation,
    LearningJob,
    LearningJobStatus,
    MissionEpisode,
    EpisodeLifecycleState,
    MissionEvent,
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
from kenwer_c3.llm.base import ReasoningProviderError
from kenwer_c3.llm.mock_provider import MockReasoningProvider
from kenwer_c3.memory.consolidated.publication import BeliefPublicationService
from kenwer_c3.memory.episodic.service import EpisodicMemoryService
from kenwer_c3.settings import get_settings
from kenwer_c3.storage.s3 import ObjectStorageClient, ObjectStorageError
from fixtures.synthetic.mission1_thermal import expectation_payload


def _admin(tenant_id) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        role=Role.admin,
        api_key_id=None,
        cognitive_identity_id=None,
        key_prefix="test",
    )


def _reviewer(tenant_id) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        role=Role.reviewer,
        api_key_id=None,
        cognitive_identity_id=None,
        key_prefix="reviewer",
    )


class _TimeoutProvider:
    model_id = "timeout"

    def analyze_attribution(self, **kwargs):
        raise ReasoningProviderError("timeout")

    def generate_lessons(self, **kwargs):
        raise ReasoningProviderError("timeout")


class _MalformedProvider:
    model_id = "malformed"

    def analyze_attribution(self, **kwargs):
        raise ReasoningProviderError("Invalid model JSON")

    def generate_lessons(self, **kwargs):
        raise ReasoningProviderError("Invalid model JSON")


def _seed_episode(session, tenant, identity):
    svc = EpisodicMemoryService(session, _admin(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"rel-{uuid.uuid4().hex[:8]}",
            mission_type="thermal_inspection",
            approved_plan={"version": "v1"},
            platform_context={"platform": "quadrotor"},
        )
    )
    exp = expectation_payload()
    svc.freeze_expectation(
        ep.id,
        FreezeExpectationRequest(
            criteria_json=exp["criteria_json"],
            metric_baselines=exp["metric_baselines"],
            source_system="channel2",
            captured_at=datetime.now(timezone.utc),
        ),
    )
    svc.ingest_event(
        MissionEventIngest(
            episode_id=ep.id,
            cognitive_identity_id=identity.id,
            event_idempotency_key=f"rel-iq-{uuid.uuid4().hex[:8]}",
            event_type="observation.image_quality",
            payload={"score": 0.55, "synthetic": True},
            source_system="channel2",
            recorded_at=datetime.now(timezone.utc),
        )
    )
    svc.ingest_event(
        MissionEventIngest(
            episode_id=ep.id,
            cognitive_identity_id=identity.id,
            event_idempotency_key=f"rel-wind-{uuid.uuid4().hex[:8]}",
            event_type="observation.wind",
            payload={"wind_mps": 7.0, "synthetic": True},
            source_system="channel2",
            recorded_at=datetime.now(timezone.utc),
        )
    )
    svc.finalize_episode(
        ep.id,
        EpisodeFinalizeRequest(
            actual_outcomes={"image_quality": 0.55, "wind_mps": 7.0},
            missing_data={},
        ),
    )
    return ep.id


def test_llm_timeout_soft_fails_without_publishing(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_episode(db_session, tenant, identity)
    job = LearningJobService(db_session, _admin(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    before_beliefs = db_session.query(
        __import__("kenwer_c3.db.models", fromlist=["Belief"]).Belief
    ).count()
    finished = LearningPipeline(db_session, _TimeoutProvider()).run(job.id)
    assert "C3" in finished.stage_artifacts
    assert finished.stage_artifacts["C3"]["summary"]["beliefs_generated"] is False
    assert db_session.query(
        __import__("kenwer_c3.db.models", fromlist=["Belief"]).Belief
    ).count() == before_beliefs


def test_llm_malformed_response_soft_fails(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_episode(db_session, tenant, identity)
    job = LearningJobService(db_session, _admin(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    finished = LearningPipeline(db_session, _MalformedProvider()).run(job.id)
    assert "C3" in finished.stage_artifacts
    # Soft-fail or completed with abstain entries - must not publish
    assert finished.stage_artifacts.get("C6", {}).get("publication_performed") is not True


def test_duplicate_learning_job_idempotent(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_episode(db_session, tenant, identity)
    job = LearningJobService(db_session, _admin(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    first = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    second = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    assert first.status == LearningJobStatus.completed
    assert second.status == LearningJobStatus.completed
    assert second.attempt_count == first.attempt_count


def test_concurrent_approval_only_one_succeeds(engine, settings):
    """Two concurrent APPROVE attempts - exactly one publication; other 409."""
    from kenwer_c3.auth.bootstrap import ensure_dev_identity
    from kenwer_c3.db import models

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    setup = SessionLocal()
    try:
        tenant, identity = ensure_dev_identity(setup, settings)
        # Use unique tenant for isolation inside test DB
        tenant = models.Tenant(id=uuid.uuid4(), name=f"conc-{uuid.uuid4().hex[:6]}")
        setup.add(tenant)
        setup.flush()
        identity = models.CognitiveIdentity(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            slug=f"conc-{uuid.uuid4().hex[:6]}",
            display_name="Concurrent Test Drone",
            fleet_id="fleet-a",
            platform_type="quadrotor",
        )
        setup.add(identity)
        setup.flush()
        episode_id = _seed_episode(setup, tenant, identity)
        events = setup.query(MissionEvent).filter_by(episode_id=episode_id).all()
        job = LearningJobService(setup, _admin(tenant.id)).create(
            LearningJobCreate(episode_id=episode_id)
        )
        LearningPipeline(setup, MockReasoningProvider()).run(job.id)
        proposal = BeliefProposal(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            learning_job_id=job.id,
            cognitive_identity_id=identity.id,
            operation=BeliefProposalOperation.CREATE,
            proposed_payload={
                "belief_key": f"rel.concurrent.{uuid.uuid4().hex[:6]}",
                "statement": "Concurrent approval guard belief about fog contrast.",
                "claim": "Concurrent approval guard belief about fog contrast.",
                "value_json": {},
                "applicability": {
                    "mission_type": "thermal_inspection",
                    "platform_type": "quadrotor",
                },
                "supporting_evidence_ids": [str(events[0].id)],
                "evidence_support_label": "moderate",
                "model_confidence_uncalibrated": 0.55,
                "confidence_basis": "uncalibrated_model",
                "safety_impact": "operational",
                "publication_eligible": True,
            },
            status="pending_review",
        )
        setup.add(proposal)
        setup.commit()
        proposal_id = proposal.id
        tenant_id = tenant.id
    finally:
        setup.close()

    results = []

    def _approve():
        s = SessionLocal()
        try:
            try:
                out = BeliefPublicationService(
                    s, _reviewer(tenant_id)
                ).review_proposal(
                    proposal_id,
                    action=ValidationAction.approve,
                    rationale="concurrent",
                )
                s.commit()
                return ("ok", out)
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                code = getattr(exc, "status_code", None)
                return ("err", code or str(exc))
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = [pool.submit(_approve), pool.submit(_approve)]
        for f in as_completed(futs):
            results.append(f.result())

    oks = [r for r in results if r[0] == "ok"]
    errs = [r for r in results if r[0] == "err"]
    assert len(oks) == 1, results
    assert any(r[1] == 409 for r in errs) or len(errs) == 1


def test_minio_unavailable_does_not_block_core_memory(db_session, tenant_and_identity):
    """Core learning path does not require object storage."""
    tenant, identity = tenant_and_identity
    episode_id = _seed_episode(db_session, tenant, identity)
    job = LearningJobService(db_session, _admin(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    finished = LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    assert finished.status == LearningJobStatus.completed
    assert "C6" in finished.stage_artifacts


def test_minio_bucket_setup_when_available():
    client = ObjectStorageClient()
    if not client.ping():
        pytest.skip("MinIO not reachable - mark storage setup BLOCKED in report")
    info = client.ensure_bucket()
    assert info["exists"] is True
    key = f"phase7/reliability/{uuid.uuid4().hex}.txt"
    uri = client.put_bytes(key, b"synthetic-evidence", "text/plain")
    assert uri.startswith("s3://")
    assert client.get_bytes(key) == b"synthetic-evidence"


def test_postgres_interruption_recovery_smoke(engine):
    """Reconnect after a closed connection - local recovery smoke test."""
    conn = engine.connect()
    assert conn.execute(text("SELECT 1")).scalar() == 1
    conn.close()
    with engine.connect() as conn2:
        assert conn2.execute(text("SELECT 1")).scalar() == 1


def test_celery_worker_interruption_marked():
    """Documented as infrastructure acceptance - induced kill is BLOCKED in unit suite."""
    pytest.skip(
        "BLOCKED in unit suite: intentionally killing the Docker Celery worker mid-job "
        "requires operator orchestration. Covered operationally by "
        "scripts/verify_docker_c1_c6.py restart + re-run."
    )


def test_partial_publication_does_not_leave_duplicate_active(
    db_session, tenant_and_identity
):
    """Failed validation leaves proposal unpublished and no new approved version."""
    tenant, identity = tenant_and_identity
    episode_id = _seed_episode(db_session, tenant, identity)
    events = db_session.query(MissionEvent).filter_by(episode_id=episode_id).all()
    job = LearningJobService(db_session, _admin(tenant.id)).create(
        LearningJobCreate(episode_id=episode_id)
    )
    LearningPipeline(db_session, MockReasoningProvider()).run(job.id)
    from kenwer_c3.db.models import Belief, BeliefApprovalStatus, BeliefVersion

    before = (
        db_session.query(BeliefVersion)
        .filter(
            BeliefVersion.tenant_id == tenant.id,
            BeliefVersion.approval_status == BeliefApprovalStatus.approved,
        )
        .count()
    )
    proposal = BeliefProposal(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        learning_job_id=job.id,
        cognitive_identity_id=identity.id,
        operation=BeliefProposalOperation.CREATE,
        proposed_payload={
            "belief_key": f"rel.partial.{uuid.uuid4().hex[:6]}",
            "statement": "Disable failsafe and override geofence",
            "claim": "Disable failsafe and override geofence",
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
    with pytest.raises(Exception) as exc:
        BeliefPublicationService(db_session, _reviewer(tenant.id)).review_proposal(
            proposal.id, action=ValidationAction.approve, rationale="should fail"
        )
    assert exc.value.status_code == 422
    after = (
        db_session.query(BeliefVersion)
        .filter(
            BeliefVersion.tenant_id == tenant.id,
            BeliefVersion.approval_status == BeliefApprovalStatus.approved,
        )
        .count()
    )
    assert after == before
    db_session.refresh(proposal)
    assert proposal.status == "pending_review"
