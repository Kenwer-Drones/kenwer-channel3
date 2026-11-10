"""Episodic memory: events, expectations immutability, finalize."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def _create_episode(client, headers, identity_id: str, mission_id: str | None = None) -> dict:
    mission_id = mission_id or f"mission-{uuid.uuid4().hex[:10]}"
    response = client.post(
        "/v1/episodes",
        headers=headers,
        json={
            "cognitive_identity_id": identity_id,
            "external_mission_id": mission_id,
            "mission_type": "thermal_inspection",
            "objectives": {"inspect": "rooftop"},
            "approved_plan": {"waypoints": 3},
            "site_context": {"site_id": "plant-7"},
            "platform_context": {"platform": "quadrotor"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_freeze_expectation_then_reject_rewrite(
    client, seeded_dev_identity, auth_headers_ingest
):
    _, identity_id = seeded_dev_identity
    episode = _create_episode(client, auth_headers_ingest, str(identity_id))
    episode_id = episode["id"]

    freeze = client.post(
        f"/v1/episodes/{episode_id}/expectations/freeze",
        headers=auth_headers_ingest,
        json={
            "criteria_json": {"image_quality_min": 0.85},
            "metric_baselines": {"image_quality": 0.9},
            "source_system": "channel1",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert freeze.status_code == 200
    assert freeze.json()["is_sealed"] is True
    assert freeze.json()["criteria_json"]["image_quality_min"] == 0.85

    # Second freeze rejected
    again = client.post(
        f"/v1/episodes/{episode_id}/expectations/freeze",
        headers=auth_headers_ingest,
        json={
            "criteria_json": {"image_quality_min": 0.10},
            "metric_baselines": {},
            "source_system": "channel1",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert again.status_code == 409

    # PUT rewrite rejected
    rewrite = client.put(
        f"/v1/episodes/{episode_id}/expectations",
        headers=auth_headers_ingest,
        json={
            "criteria_json": {"image_quality_min": 0.10},
            "metric_baselines": {},
            "source_system": "channel1",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert rewrite.status_code == 409
    assert "immutable" in rewrite.json()["detail"].lower()

    # Original criteria preserved
    got = client.get(
        f"/v1/episodes/{episode_id}", headers=auth_headers_ingest
    ).json()
    assert got["expectation"]["criteria_json"]["image_quality_min"] == 0.85


def test_idempotent_event_ingest(
    client, seeded_dev_identity, auth_headers_ingest
):
    _, identity_id = seeded_dev_identity
    episode = _create_episode(client, auth_headers_ingest, str(identity_id))
    episode_id = episode["id"]
    client.post(
        f"/v1/episodes/{episode_id}/expectations/freeze",
        headers=auth_headers_ingest,
        json={
            "criteria_json": {"image_quality_min": 0.85},
            "metric_baselines": {},
            "source_system": "channel1",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    event_body = {
        "episode_id": episode_id,
        "cognitive_identity_id": str(identity_id),
        "event_idempotency_key": "evt-quality-001",
        "event_type": "observation.image_quality",
        "payload": {"score": 0.62, "wind_mps": 7.5},
        "source_system": "channel2",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "proposed_persistent_fact_key": "payload.thermal_camera",
    }
    first = client.post("/v1/memory/events", headers=auth_headers_ingest, json=event_body)
    second = client.post("/v1/memory/events", headers=auth_headers_ingest, json=event_body)
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["event"]["provenance"]["persistent_write_deferred"] is True
    assert second.status_code == 200
    assert second.json()["created"] is False

    events = client.get(
        f"/v1/episodes/{episode_id}/events", headers=auth_headers_ingest
    ).json()
    assert len(events) == 1


def test_finalize_requires_sealed_expectation(
    client, seeded_dev_identity, auth_headers_ingest
):
    _, identity_id = seeded_dev_identity
    episode = _create_episode(client, auth_headers_ingest, str(identity_id))
    bad = client.post(
        f"/v1/episodes/{episode['id']}/finalize",
        headers=auth_headers_ingest,
        json={"actual_outcomes": {"ok": False}, "missing_data": {}},
    )
    assert bad.status_code == 409

    client.post(
        f"/v1/episodes/{episode['id']}/expectations/freeze",
        headers=auth_headers_ingest,
        json={
            "criteria_json": {"image_quality_min": 0.85},
            "metric_baselines": {},
            "source_system": "channel1",
            "captured_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    good = client.post(
        f"/v1/episodes/{episode['id']}/finalize",
        headers=auth_headers_ingest,
        json={
            "actual_outcomes": {"image_quality": 0.62},
            "missing_data": {"gps_fix": "unavailable"},
        },
    )
    assert good.status_code == 200
    assert good.json()["lifecycle_state"] == "finalized"
    assert good.json()["missing_data"]["gps_fix"] == "unavailable"

    # No more events after finalize
    evt = client.post(
        "/v1/memory/events",
        headers=auth_headers_ingest,
        json={
            "episode_id": episode["id"],
            "cognitive_identity_id": str(identity_id),
            "event_idempotency_key": "late-event",
            "event_type": "observation.late",
            "payload": {},
            "source_system": "channel2",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert evt.status_code == 409
