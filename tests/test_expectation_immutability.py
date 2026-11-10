"""Invariant: sealed expectations cannot be mutated via service or SQL UPDATE."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from kenwer_c3.auth.context import Principal
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.episodic import FreezeExpectationRequest, MissionEpisodeCreate
from kenwer_c3.memory.episodic.service import EpisodicMemoryService


def test_db_trigger_blocks_sealed_expectation_update(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    principal = Principal(
        tenant_id=tenant.id,
        role=Role.admin,
        api_key_id=None,
        cognitive_identity_id=None,
        key_prefix="test",
    )
    svc = EpisodicMemoryService(db_session, principal)
    episode = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"imm-{uuid.uuid4().hex[:8]}",
            mission_type="thermal_inspection",
            approved_plan={"version": "v1"},
        )
    )
    snap = svc.freeze_expectation(
        episode.id,
        FreezeExpectationRequest(
            criteria_json={"image_quality_min": 0.85},
            metric_baselines={},
            mission_plan_version="v1",
            source_system="channel1",
            captured_at=datetime.now(timezone.utc),
        ),
    )
    assert snap.is_sealed is True

    with pytest.raises((DBAPIError, Exception)):
        db_session.execute(
            text(
                "UPDATE expectation_snapshots SET criteria_json = CAST(:c AS jsonb) "
                "WHERE id = CAST(:id AS uuid)"
            ),
            {"c": '{"image_quality_min": 0.1}', "id": str(snap.id)},
        )
        db_session.flush()


def test_observation_requires_frozen_expectation(
    client, seeded_dev_identity, auth_headers_ingest
):
    _, identity_id = seeded_dev_identity
    ep = client.post(
        "/v1/episodes",
        headers=auth_headers_ingest,
        json={
            "cognitive_identity_id": str(identity_id),
            "external_mission_id": f"obs-gate-{uuid.uuid4().hex[:8]}",
            "mission_type": "thermal_inspection",
            "approved_plan": {"version": "v1"},
        },
    )
    assert ep.status_code == 200
    episode_id = ep.json()["id"]

    blocked = client.post(
        "/v1/memory/events",
        headers=auth_headers_ingest,
        json={
            "episode_id": episode_id,
            "cognitive_identity_id": str(identity_id),
            "event_idempotency_key": f"early-{uuid.uuid4().hex[:6]}",
            "event_type": "observation.image_quality",
            "payload": {"score": 0.5},
            "source_system": "channel2",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert blocked.status_code == 409
