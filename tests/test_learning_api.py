"""Authenticated learning job API tests (eager Celery)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fixtures.synthetic.mission1_thermal import (
    episode_payload,
    expectation_payload,
    finalize_payload,
    observation_events,
)


def _prepare_mission(client, headers, identity_id: str) -> str:
    ep = client.post(
        "/v1/episodes",
        headers=headers,
        json=episode_payload(identity_id, f"api-m1-{uuid.uuid4().hex[:8]}"),
    )
    assert ep.status_code == 200, ep.text
    episode_id = ep.json()["id"]
    freeze = client.post(
        f"/v1/episodes/{episode_id}/expectations/freeze",
        headers=headers,
        json=expectation_payload(),
    )
    assert freeze.status_code == 200, freeze.text
    for evt in observation_events(
        episode_id=episode_id, cognitive_identity_id=identity_id
    ):
        r = client.post("/v1/memory/events", headers=headers, json=evt)
        assert r.status_code == 200, r.text
    fin = client.post(
        f"/v1/episodes/{episode_id}/finalize",
        headers=headers,
        json=finalize_payload(),
    )
    assert fin.status_code == 200, fin.text
    return episode_id


def test_learning_job_api_eager_runs_c1_c3(
    client, seeded_dev_identity, auth_headers_admin
):
    _, identity_id = seeded_dev_identity
    episode_id = _prepare_mission(client, auth_headers_admin, str(identity_id))

    created = client.post(
        "/v1/learning/jobs",
        headers=auth_headers_admin,
        json={"episode_id": episode_id, "idempotency_key": f"job-{uuid.uuid4().hex}"},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "completed"
    assert body["stage_artifacts"]["C3"]["summary"]["beliefs_generated"] is False
    reports = body["stage_artifacts"]["C3"]["reports"]
    iq = next(r for r in reports if r["metric_name"] == "image_quality")
    assert iq["result_status"] == "fail"
    assert iq["observed_value"]["value"] == 0.62

    got = client.get(f"/v1/learning/jobs/{body['id']}", headers=auth_headers_admin)
    assert got.status_code == 200
    assert got.json()["status"] == "completed"


def test_learning_job_unauthorized(client, seeded_dev_identity, auth_headers_retrieve):
    response = client.post(
        "/v1/learning/jobs",
        headers=auth_headers_retrieve,
        json={"episode_id": str(uuid.uuid4())},
    )
    assert response.status_code == 403


def test_learning_job_cross_tenant_denied(
    client, engine, seeded_dev_identity, auth_headers_admin
):
    from kenwer_c3.db import models
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        other = models.Tenant(id=uuid.uuid4(), name=f"lj-{uuid.uuid4().hex[:6]}")
        session.add(other)
        session.flush()
        ident = models.CognitiveIdentity(
            id=uuid.uuid4(),
            tenant_id=other.id,
            slug="x",
            display_name="x",
        )
        session.add(ident)
        session.flush()
        ep = models.MissionEpisode(
            id=uuid.uuid4(),
            tenant_id=other.id,
            cognitive_identity_id=ident.id,
            external_mission_id=f"x-{uuid.uuid4().hex[:6]}",
            mission_type="thermal_inspection",
        )
        session.add(ep)
        session.commit()
        foreign_id = ep.id
    finally:
        session.close()

    response = client.post(
        "/v1/learning/jobs",
        headers=auth_headers_admin,
        json={"episode_id": str(foreign_id)},
    )
    assert response.status_code == 404
