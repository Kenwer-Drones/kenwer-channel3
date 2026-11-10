"""Consolidated memory read path - approved only."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from kenwer_c3.db import models
from kenwer_c3.settings import get_settings


def _seed_beliefs(engine, identity_id: uuid.UUID, tenant_id: uuid.UUID) -> uuid.UUID:
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        belief = models.Belief(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            cognitive_identity_id=identity_id,
            belief_key=f"thermal.wind_degrades_image_quality.{uuid.uuid4().hex[:8]}",
        )
        session.add(belief)
        session.flush()

        proposed = models.BeliefVersion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            belief_id=belief.id,
            cognitive_identity_id=identity_id,
            version=1,
            statement="Unreviewed hypothesis - must not appear in retrieval.",
            value_json={"wind_limit_mps": 6.0},
            applicability={"mission_type": "thermal_inspection"},
            confidence=0.4,
            approval_status=models.BeliefApprovalStatus.proposed,
            safety_impact=models.SafetyImpact.operational,
        )
        approved = models.BeliefVersion(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            belief_id=belief.id,
            cognitive_identity_id=identity_id,
            version=2,
            statement="Wind above 8 m/s degrades thermal image quality for this platform.",
            value_json={"wind_limit_mps": 8.0},
            applicability={"mission_type": "thermal_inspection"},
            confidence=0.8,
            approval_status=models.BeliefApprovalStatus.approved,
            safety_impact=models.SafetyImpact.operational,
            published_at=datetime.now(timezone.utc),
            limitations="Validated on quay-side inspections only.",
        )
        session.add_all([proposed, approved])
        belief.current_version_id = approved.id
        session.commit()
        return belief.id
    finally:
        session.close()


def test_list_beliefs_returns_only_approved(
    client, engine, seeded_dev_identity, auth_headers_retrieve
):
    tenant_id, identity_id = seeded_dev_identity
    belief_id = _seed_beliefs(engine, identity_id, tenant_id)

    response = client.get(
        "/v1/beliefs",
        params={"cognitive_identity_id": str(identity_id)},
        headers=auth_headers_retrieve,
    )
    assert response.status_code == 200
    rows = [r for r in response.json() if r["id"] == str(belief_id)]
    assert len(rows) == 1
    assert rows[0]["current_version"]["approval_status"] == "approved"
    assert rows[0]["current_version"]["version"] == 2
    assert "Unreviewed" not in rows[0]["current_version"]["statement"]

    # Direct get also returns approved version only
    got = client.get(f"/v1/beliefs/{belief_id}", headers=auth_headers_retrieve)
    assert got.status_code == 200
    assert got.json()["current_version"]["version"] == 2


def test_get_belief_hides_unapproved_only_belief(
    client, engine, seeded_dev_identity, auth_headers_retrieve
):
    from sqlalchemy.orm import sessionmaker

    tenant_id, identity_id = seeded_dev_identity
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        belief = models.Belief(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            cognitive_identity_id=identity_id,
            belief_key=f"thermal.unapproved_only.{uuid.uuid4().hex[:8]}",
        )
        session.add(belief)
        session.flush()
        session.add(
            models.BeliefVersion(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                belief_id=belief.id,
                cognitive_identity_id=identity_id,
                version=1,
                statement="Proposed only",
                value_json={},
                applicability={},
                confidence=0.2,
                approval_status=models.BeliefApprovalStatus.proposed,
                safety_impact=models.SafetyImpact.operational,
            )
        )
        session.commit()
        belief_id = belief.id
    finally:
        session.close()

    response = client.get(
        f"/v1/beliefs/{belief_id}",
        headers=auth_headers_retrieve,
    )
    assert response.status_code == 404


def test_applicability_filter(
    client, engine, seeded_dev_identity, auth_headers_retrieve
):
    tenant_id, identity_id = seeded_dev_identity
    belief_id = _seed_beliefs(engine, identity_id, tenant_id)

    match = client.get(
        "/v1/beliefs",
        params={
            "cognitive_identity_id": str(identity_id),
            "mission_type": "thermal_inspection",
        },
        headers=auth_headers_retrieve,
    )
    assert match.status_code == 200
    assert any(r["id"] == str(belief_id) for r in match.json())

    miss = client.get(
        "/v1/beliefs",
        params={
            "cognitive_identity_id": str(identity_id),
            "mission_type": "mapping",
        },
        headers=auth_headers_retrieve,
    )
    assert miss.status_code == 200
    assert all(r["id"] != str(belief_id) for r in miss.json())
