"""Cross-tenant isolation tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from kenwer_c3.db import models
from kenwer_c3.settings import get_settings


def test_cannot_read_other_tenant_episode_via_api(
    client, engine, seeded_dev_identity, auth_headers_admin
):
    """Dev credentials are bound to the dev tenant; foreign episode IDs 404."""
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        other_tenant = models.Tenant(id=uuid.uuid4(), name=f"iso-{uuid.uuid4().hex[:6]}")
        session.add(other_tenant)
        session.flush()
        other_identity = models.CognitiveIdentity(
            id=uuid.uuid4(),
            tenant_id=other_tenant.id,
            slug="iso-drone",
            display_name="Isolated",
        )
        session.add(other_identity)
        session.flush()
        episode = models.MissionEpisode(
            id=uuid.uuid4(),
            tenant_id=other_tenant.id,
            cognitive_identity_id=other_identity.id,
            external_mission_id=f"secret-{uuid.uuid4().hex[:6]}",
            mission_type="thermal_inspection",
        )
        session.add(episode)
        session.commit()
        foreign_episode_id = episode.id
    finally:
        session.close()

    response = client.get(
        f"/v1/episodes/{foreign_episode_id}",
        headers=auth_headers_admin,
    )
    assert response.status_code == 404


def test_cannot_write_fact_for_foreign_identity(
    client, engine, seeded_dev_identity, auth_headers_ingest
):
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        other_tenant = models.Tenant(id=uuid.uuid4(), name=f"iso2-{uuid.uuid4().hex[:6]}")
        session.add(other_tenant)
        session.flush()
        other_identity = models.CognitiveIdentity(
            id=uuid.uuid4(),
            tenant_id=other_tenant.id,
            slug="iso2-drone",
            display_name="Isolated 2",
        )
        session.add(other_identity)
        session.commit()
        foreign_identity_id = other_identity.id
    finally:
        session.close()

    response = client.post(
        "/v1/memory/facts",
        headers=auth_headers_ingest,
        json={
            "cognitive_identity_id": str(foreign_identity_id),
            "fact_key": "stolen.fact",
            "category": "test",
            "value_json": {"x": 1},
            "source_system": "evil",
        },
    )
    # Bound identity mismatch or not found in tenant → 403 or 404
    assert response.status_code in (403, 404)


def test_service_layer_tenant_filter(db_session, tenant_and_identity, other_tenant_identity):
    from kenwer_c3.auth.context import Principal
    from kenwer_c3.domain.common import Role
    from kenwer_c3.memory.episodic.service import EpisodicMemoryService
    from kenwer_c3.domain.episodic import MissionEpisodeCreate

    tenant, identity = tenant_and_identity
    other_tenant, other_identity = other_tenant_identity

    principal = Principal(
        tenant_id=tenant.id,
        role=Role.admin,
        api_key_id=None,
        cognitive_identity_id=None,
        key_prefix="test",
    )
    svc = EpisodicMemoryService(db_session, principal)
    created = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"local-{uuid.uuid4().hex[:6]}",
            mission_type="thermal_inspection",
        )
    )
    assert created.tenant_id == tenant.id

    # Foreign identity under other tenant is not visible
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as exc:
        svc.create_episode(
            MissionEpisodeCreate(
                cognitive_identity_id=other_identity.id,
                external_mission_id=f"cross-{uuid.uuid4().hex[:6]}",
                mission_type="thermal_inspection",
            )
        )
    assert exc.value.status_code == 404
