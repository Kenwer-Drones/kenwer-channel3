"""Persistent memory API and service tests."""

from __future__ import annotations

import uuid


def _fact_body(
    identity_id: str,
    value: dict,
    *,
    allow: bool = False,
    fact_key: str | None = None,
) -> dict:
    return {
        "cognitive_identity_id": identity_id,
        "fact_key": fact_key or f"payload.thermal_camera.{uuid.uuid4().hex[:8]}",
        "category": "payload",
        "value_json": value,
        "source_system": "channel2",
        "source_event_id": "cfg-1",
        "provenance": {"operator": "qa"},
        "allow_controlled_update": allow,
    }


def test_create_and_list_persistent_fact(
    client, seeded_dev_identity, auth_headers_ingest, auth_headers_retrieve
):
    _, identity_id = seeded_dev_identity
    fact_key = f"payload.list.{uuid.uuid4().hex[:8]}"
    create = client.post(
        "/v1/memory/facts",
        headers=auth_headers_ingest,
        json=_fact_body(
            str(identity_id),
            {"model": "FLIR-X", "resolution": "640x512"},
            fact_key=fact_key,
        ),
    )
    assert create.status_code == 200
    body = create.json()
    assert body["created"] is True
    assert body["fact"]["version"] == 1
    assert body["fact"]["status"] == "active"

    listed = client.get(
        "/v1/memory/facts",
        params={"cognitive_identity_id": str(identity_id), "fact_key": fact_key},
        headers=auth_headers_retrieve,
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == body["fact"]["id"]


def test_observation_cannot_silently_overwrite_fact(
    client, seeded_dev_identity, auth_headers_ingest
):
    _, identity_id = seeded_dev_identity
    fact_key = f"payload.no_silent.{uuid.uuid4().hex[:8]}"
    first = client.post(
        "/v1/memory/facts",
        headers=auth_headers_ingest,
        json=_fact_body(str(identity_id), {"model": "FLIR-X"}, fact_key=fact_key),
    )
    assert first.status_code == 200

    conflict = client.post(
        "/v1/memory/facts",
        headers=auth_headers_ingest,
        json=_fact_body(
            str(identity_id), {"model": "FLIR-Y"}, allow=False, fact_key=fact_key
        ),
    )
    assert conflict.status_code == 409
    detail = conflict.json()["detail"].lower()
    assert "silently overwrite" in detail or "differ" in detail

    listed = client.get(
        "/v1/memory/facts",
        params={
            "cognitive_identity_id": str(identity_id),
            "fact_key": fact_key,
            "active_only": True,
        },
        headers=auth_headers_ingest,
    )
    assert listed.status_code == 200
    assert listed.json()[0]["value_json"]["model"] == "FLIR-X"
    assert listed.json()[0]["version"] == 1


def test_controlled_update_versions_fact(
    client, seeded_dev_identity, auth_headers_ingest
):
    _, identity_id = seeded_dev_identity
    fact_key = f"payload.controlled.{uuid.uuid4().hex[:8]}"
    client.post(
        "/v1/memory/facts",
        headers=auth_headers_ingest,
        json=_fact_body(str(identity_id), {"model": "FLIR-X"}, fact_key=fact_key),
    )
    updated = client.post(
        "/v1/memory/facts",
        headers=auth_headers_ingest,
        json=_fact_body(
            str(identity_id), {"model": "FLIR-Y"}, allow=True, fact_key=fact_key
        ),
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["created"] is True
    assert body["contradiction_detected"] is True
    assert body["fact"]["version"] == 2
    assert body["fact"]["value_json"]["model"] == "FLIR-Y"

    active = client.get(
        "/v1/memory/facts",
        params={
            "cognitive_identity_id": str(identity_id),
            "fact_key": fact_key,
            "active_only": True,
        },
        headers=auth_headers_ingest,
    ).json()
    assert len(active) == 1
    assert active[0]["version"] == 2

    all_versions = client.get(
        "/v1/memory/facts",
        params={
            "cognitive_identity_id": str(identity_id),
            "fact_key": fact_key,
            "active_only": False,
        },
        headers=auth_headers_ingest,
    ).json()
    assert len(all_versions) == 2


def test_idempotent_identical_fact_write(
    client, seeded_dev_identity, auth_headers_ingest
):
    _, identity_id = seeded_dev_identity
    payload = _fact_body(str(identity_id), {"model": "FLIR-X"})
    first = client.post("/v1/memory/facts", headers=auth_headers_ingest, json=payload)
    second = client.post("/v1/memory/facts", headers=auth_headers_ingest, json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["fact"]["version"] == 1
