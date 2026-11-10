"""Database schema and architectural constraint smoke tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from kenwer_c3.db import models


REQUIRED_TABLES = {
    # identity / auth
    "tenants",
    "cognitive_identities",
    "api_keys",
    "audit_logs",
    # B1 persistent
    "persistent_facts",
    # B2 episodic
    "mission_episodes",
    "expectation_snapshots",
    "mission_events",
    "evidence_artifacts",
    # B3 consolidated
    "beliefs",
    "belief_versions",
    "belief_evidence_links",
    "validation_decisions",
    # learning pipeline (not consolidated)
    "learning_jobs",
    "discrepancy_reports",
    "attribution_hypotheses",
    "candidate_lessons",
    "belief_proposals",
}


def test_all_required_tables_exist(engine):
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    missing = REQUIRED_TABLES - existing
    assert not missing, f"Missing tables: {sorted(missing)}"


def test_pgvector_extension_installed(engine):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).fetchone()
    assert row is not None


def test_three_memory_families_are_separate_tables(engine):
    """Persistent / episodic / consolidated must not share a single dump table."""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "persistent_facts" in tables
    assert "mission_episodes" in tables
    assert "mission_events" in tables
    assert "beliefs" in tables
    assert "belief_versions" in tables
    # Explicitly reject a collapsed generic memory store
    assert "memories" not in tables
    assert "cognitive_memory" not in tables


def test_tenant_and_identity_scoping_insert(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    fact = models.PersistentFact(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        fact_key="payload.thermal_camera",
        category="payload",
        value_json={"model": "FLIR-X", "resolution": "640x512"},
        source_system="channel2",
        version=1,
        status=models.FactStatus.active,
    )
    db_session.add(fact)
    db_session.flush()

    episode = models.MissionEpisode(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        external_mission_id=f"mission-{uuid.uuid4().hex[:8]}",
        mission_type="thermal_inspection",
        objectives={"inspect": "rooftop_hvac"},
        approved_plan={"waypoints": 4},
        lifecycle_state=models.EpisodeLifecycleState.planned,
    )
    db_session.add(episode)
    db_session.flush()

    belief = models.Belief(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        belief_key="thermal.image_quality.min_score",
    )
    db_session.add(belief)
    db_session.flush()

    assert fact.id is not None
    assert episode.cognitive_identity_id == identity.id
    assert belief.tenant_id == tenant.id


def test_expectation_snapshot_one_per_episode(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode = models.MissionEpisode(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        external_mission_id=f"mission-{uuid.uuid4().hex[:8]}",
        mission_type="thermal_inspection",
        lifecycle_state=models.EpisodeLifecycleState.active,
    )
    db_session.add(episode)
    db_session.flush()

    snap = models.ExpectationSnapshot(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        episode_id=episode.id,
        cognitive_identity_id=identity.id,
        criteria_json={"image_quality_min": 0.85},
        metric_baselines={"image_quality": 0.9},
        source_system="channel1",
        captured_at=datetime.now(timezone.utc),
        is_sealed=True,
    )
    db_session.add(snap)
    db_session.flush()

    dup = models.ExpectationSnapshot(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        episode_id=episode.id,
        cognitive_identity_id=identity.id,
        criteria_json={"image_quality_min": 0.5},  # would be a retroactive rewrite
        metric_baselines={},
        source_system="channel1",
        captured_at=datetime.now(timezone.utc),
        is_sealed=True,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_mission_event_idempotency(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode = models.MissionEpisode(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        external_mission_id=f"mission-{uuid.uuid4().hex[:8]}",
        mission_type="thermal_inspection",
    )
    db_session.add(episode)
    db_session.flush()

    key = "evt-thermal-frame-001"
    event = models.MissionEvent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        episode_id=episode.id,
        cognitive_identity_id=identity.id,
        event_idempotency_key=key,
        event_type="observation.image_quality",
        payload={"score": 0.62},
        source_system="channel2",
        recorded_at=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.flush()

    duplicate = models.MissionEvent(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        episode_id=episode.id,
        cognitive_identity_id=identity.id,
        event_idempotency_key=key,
        event_type="observation.image_quality",
        payload={"score": 0.62},
        source_system="channel2",
        recorded_at=datetime.now(timezone.utc),
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_belief_version_requires_approval_status_default(
    db_session, tenant_and_identity
):
    """New belief versions start as proposed - not auto-approved into consolidated use."""
    tenant, identity = tenant_and_identity
    belief = models.Belief(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        belief_key="thermal.wind_limit_mps",
    )
    db_session.add(belief)
    db_session.flush()

    version = models.BeliefVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        belief_id=belief.id,
        cognitive_identity_id=identity.id,
        version=1,
        statement="Wind above 8 m/s degrades thermal image quality for this platform.",
        value_json={"wind_limit_mps": 8.0},
        applicability={"mission_type": "thermal_inspection"},
        confidence=0.7,
        safety_impact=models.SafetyImpact.operational,
    )
    db_session.add(version)
    db_session.flush()

    assert version.approval_status == models.BeliefApprovalStatus.proposed
    assert version.published_at is None


def test_metadata_reflects_memory_family_separation():
    """ORM module documents three separate model families."""
    persistent = {models.PersistentFact.__tablename__}
    episodic = {
        models.MissionEpisode.__tablename__,
        models.ExpectationSnapshot.__tablename__,
        models.MissionEvent.__tablename__,
        models.EvidenceArtifact.__tablename__,
    }
    consolidated = {
        models.Belief.__tablename__,
        models.BeliefVersion.__tablename__,
        models.BeliefEvidenceLink.__tablename__,
        models.ValidationDecision.__tablename__,
    }
    assert persistent.isdisjoint(episodic)
    assert persistent.isdisjoint(consolidated)
    assert episodic.isdisjoint(consolidated)
