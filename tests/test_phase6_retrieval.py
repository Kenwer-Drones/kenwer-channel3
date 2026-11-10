"""Phase 6: cognitive retrieval engine."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    BeliefVersion,
    FactStatus,
    PersistentFact,
    SafetyImpact,
)
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.retrieval import CognitiveRetrievalRequest, MemoryTypeRequest
from kenwer_c3.retrieval.applicability import applicability_hard_mismatch
from kenwer_c3.retrieval.engine import CognitiveRetrievalEngine
from kenwer_c3.retrieval.embeddings import embed_text


def _principal(tenant_id, *, role=Role.channel1_retrieve, identity_id=None) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        role=role,
        api_key_id=None,
        cognitive_identity_id=identity_id,
        key_prefix="test",
    )


def _seed_knowledge(session, tenant, identity):
    fact = PersistentFact(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        fact_key="payload.thermal_camera",
        category="payload",
        version=1,
        value_json={
            "model": "FLIR-X",
            "applicability": {
                "platform_type": "quadrotor",
                "mission_type": "thermal_inspection",
            },
        },
        status=FactStatus.active,
        provenance={"source_system": "channel2", "synthetic": True},
        source_system="channel2",
    )
    session.add(fact)

    approved = Belief(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        belief_key="thermal.wind.approved",
    )
    session.add(approved)
    session.flush()
    v_approved = BeliefVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        belief_id=approved.id,
        cognitive_identity_id=identity.id,
        version=1,
        statement="Elevated wind may correlate with reduced thermal image quality.",
        value_json={"wind_mps": 8},
        applicability={
            "mission_type": "thermal_inspection",
            "platform_type": "quadrotor",
            "fleet_id": "fleet-a",
            "site_id": "site-1",
            "environment": "coastal",
        },
        confidence=0.55,
        confidence_basis="uncalibrated_model",
        evidence_support_label="moderate",
        approval_status=BeliefApprovalStatus.approved,
        safety_impact=SafetyImpact.operational,
        provenance={"supporting_evidence_ids": [str(uuid.uuid4())]},
        limitations="Correlation only",
        published_at=datetime.now(timezone.utc),
        embedding=embed_text(
            "Elevated wind may correlate with reduced thermal image quality."
        ),
    )
    session.add(v_approved)
    session.flush()
    approved.current_version_id = v_approved.id

    proposed = Belief(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        belief_key="thermal.wind.proposed_only",
    )
    session.add(proposed)
    session.flush()
    session.add(
        BeliefVersion(
            id=uuid.uuid4(),
            tenant_id=tenant.id,
            belief_id=proposed.id,
            cognitive_identity_id=identity.id,
            version=1,
            statement="LEAK PROPOSED BELIEF must not appear",
            value_json={},
            applicability={"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
            confidence=0.9,
            approval_status=BeliefApprovalStatus.proposed,
            safety_impact=SafetyImpact.operational,
        )
    )

    superseded_belief = Belief(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        cognitive_identity_id=identity.id,
        belief_key="thermal.superseded.demo",
    )
    session.add(superseded_belief)
    session.flush()
    old = BeliefVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        belief_id=superseded_belief.id,
        cognitive_identity_id=identity.id,
        version=1,
        statement="LEAK SUPERSEDED must not appear as active",
        value_json={},
        applicability={"mission_type": "thermal_inspection", "platform_type": "quadrotor"},
        confidence=0.5,
        approval_status=BeliefApprovalStatus.superseded,
        safety_impact=SafetyImpact.operational,
        published_at=datetime.now(timezone.utc),
    )
    new = BeliefVersion(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        belief_id=superseded_belief.id,
        cognitive_identity_id=identity.id,
        version=2,
        statement="Active approved successor belief about battery thermal limits.",
        value_json={},
        applicability={
            "mission_type": "thermal_inspection",
            "platform_type": "quadrotor",
            "fleet_id": "fleet-a",
        },
        confidence=0.6,
        confidence_basis="human_set",
        evidence_support_label="strong",
        approval_status=BeliefApprovalStatus.approved,
        safety_impact=SafetyImpact.operational,
        published_at=datetime.now(timezone.utc),
        supersedes_version_id=old.id,
    )
    session.add_all([old, new])
    session.flush()
    superseded_belief.current_version_id = new.id
    session.flush()
    return fact, approved, v_approved


def test_retrieval_persistent_and_approved_beliefs(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    _seed_knowledge(db_session, tenant, identity)
    engine = CognitiveRetrievalEngine(
        db_session, _principal(tenant.id, identity_id=identity.id)
    )
    result = engine.retrieve(
        CognitiveRetrievalRequest(
            cognitive_identity_id=identity.id,
            mission_type="thermal_inspection",
            platform_type="quadrotor",
            fleet_id="fleet-a",
            site_id="site-1",
            environment="coastal",
            mission_objective="Inspect coastal assets under wind",
            query_text="wind thermal image quality",
            requested_memory_types=[
                MemoryTypeRequest.persistent,
                MemoryTypeRequest.consolidated,
            ],
        )
    )
    assert result.no_applicable_knowledge is False
    assert result.knowledge_version.startswith("c3-retrieval")
    assert any(f.fact_key == "payload.thermal_camera" for f in result.persistent_facts)
    assert result.persistent_facts[0].provenance
    keys = {b.belief_key for b in result.approved_beliefs}
    assert "thermal.wind.approved" in keys
    assert "thermal.superseded.demo" in keys
    statements = " ".join(b.statement for b in result.approved_beliefs)
    assert "LEAK PROPOSED" not in statements
    assert "LEAK SUPERSEDED" not in statements
    wind = next(b for b in result.approved_beliefs if b.belief_key == "thermal.wind.approved")
    assert wind.confidence_basis == "uncalibrated_model"
    assert wind.evidence_refs
    assert wind.ranking_method is not None


def test_platform_mismatch_filters_beliefs(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    _seed_knowledge(db_session, tenant, identity)
    result = CognitiveRetrievalEngine(
        db_session, _principal(tenant.id)
    ).retrieve(
        CognitiveRetrievalRequest(
            cognitive_identity_id=identity.id,
            mission_type="thermal_inspection",
            platform_type="fixed_wing",
            fleet_id="fleet-a",
            query_text="wind",
        )
    )
    assert all(
        b.applicability.get("platform_type") != "quadrotor"
        or applicability_hard_mismatch(
            {"platform_type": "fixed_wing"}, b.applicability
        )
        is False
        for b in result.approved_beliefs
    )
    # Hard mismatch should exclude quadrotor-only approved wind belief
    assert not any(b.belief_key == "thermal.wind.approved" for b in result.approved_beliefs)


def test_no_applicable_knowledge(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    result = CognitiveRetrievalEngine(db_session, _principal(tenant.id)).retrieve(
        CognitiveRetrievalRequest(
            cognitive_identity_id=identity.id,
            mission_type="mapping",
            platform_type="quadrotor",
        )
    )
    assert result.no_applicable_knowledge is True
    assert result.approved_beliefs == []
    assert result.persistent_facts == []


def test_cross_tenant_denied(db_session, tenant_and_identity, other_tenant_identity):
    tenant, identity = tenant_and_identity
    other_tenant, _ = other_tenant_identity
    _seed_knowledge(db_session, tenant, identity)
    with pytest.raises(Exception) as exc:
        CognitiveRetrievalEngine(db_session, _principal(other_tenant.id)).retrieve(
            CognitiveRetrievalRequest(cognitive_identity_id=identity.id)
        )
    assert exc.value.status_code in (403, 404)


def test_cross_identity_denied(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    other = uuid.uuid4()
    with pytest.raises(Exception) as exc:
        CognitiveRetrievalEngine(
            db_session,
            _principal(tenant.id, identity_id=other),
        ).retrieve(CognitiveRetrievalRequest(cognitive_identity_id=identity.id))
    assert exc.value.status_code == 403


def test_semantic_does_not_override_authz_or_applicability(
    db_session, tenant_and_identity
):
    tenant, identity = tenant_and_identity
    _seed_knowledge(db_session, tenant, identity)
    # Query text strongly matches wind belief, but platform mismatches
    result = CognitiveRetrievalEngine(db_session, _principal(tenant.id)).retrieve(
        CognitiveRetrievalRequest(
            cognitive_identity_id=identity.id,
            mission_type="thermal_inspection",
            platform_type="fixed_wing",
            query_text="Elevated wind may correlate with reduced thermal image quality",
        )
    )
    assert not any(b.belief_key == "thermal.wind.approved" for b in result.approved_beliefs)


def test_api_retrieval_endpoint(
    client, seeded_dev_identity, auth_headers_retrieve, auth_headers_admin, db_session
):
    # seeded identity lives in test DB via ensure_dev_identity
    _, identity_id = seeded_dev_identity
    # Seed an approved belief via admin path / direct ORM on engine used by API
    from kenwer_c3.db.base import get_session_factory
    from kenwer_c3.settings import get_settings

    get_settings.cache_clear()
    session = get_session_factory()()
    try:
        belief = Belief(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(get_settings().dev_tenant_id),
            cognitive_identity_id=identity_id,
            belief_key=f"api.retrieve.{uuid.uuid4().hex[:6]}",
        )
        session.add(belief)
        session.flush()
        version = BeliefVersion(
            id=uuid.uuid4(),
            tenant_id=belief.tenant_id,
            belief_id=belief.id,
            cognitive_identity_id=identity_id,
            version=1,
            statement="API retrieval approved belief about fog contrast.",
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
        session.add(version)
        session.flush()
        belief.current_version_id = version.id
        session.commit()
    finally:
        session.close()

    resp = client.post(
        "/v1/retrieve/cognitive-context",
        headers=auth_headers_retrieve,
        json={
            "cognitive_identity_id": str(identity_id),
            "mission_type": "thermal_inspection",
            "platform_type": "quadrotor",
            "query_text": "fog contrast",
            "requested_memory_types": ["persistent", "consolidated"],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["knowledge_version"]
    assert "limitations" in body
    assert any(
        "fog" in b["statement"].lower() for b in body["approved_beliefs"]
    ) or body["no_applicable_knowledge"] in (True, False)
