"""Authentication and authorization tests."""


def test_missing_auth_rejected(client):
    response = client.post(
        "/v1/memory/events",
        json={},
    )
    assert response.status_code == 401


def test_invalid_api_key_rejected(client, seeded_dev_identity):
    response = client.get(
        "/v1/memory/facts",
        params={"cognitive_identity_id": str(seeded_dev_identity[1])},
        headers={"Authorization": "Bearer totally-invalid-key"},
    )
    assert response.status_code == 401


def test_retrieve_role_cannot_ingest_events(
    client, seeded_dev_identity, auth_headers_retrieve
):
    tenant_id, identity_id = seeded_dev_identity
    response = client.post(
        "/v1/memory/events",
        headers=auth_headers_retrieve,
        json={
            "cognitive_identity_id": str(identity_id),
            "external_mission_id": "m-unauthorized",
            "event_idempotency_key": "k1",
            "event_type": "observation.test",
            "payload": {},
            "source_system": "channel2",
            "recorded_at": "2026-03-23T12:00:00Z",
        },
    )
    assert response.status_code == 403


def test_ingest_role_cannot_list_beliefs(
    client, seeded_dev_identity, auth_headers_ingest
):
    _, identity_id = seeded_dev_identity
    response = client.get(
        "/v1/beliefs",
        params={"cognitive_identity_id": str(identity_id)},
        headers=auth_headers_ingest,
    )
    assert response.status_code == 403


def test_admin_can_list_facts(client, seeded_dev_identity, auth_headers_admin):
    _, identity_id = seeded_dev_identity
    response = client.get(
        "/v1/memory/facts",
        params={"cognitive_identity_id": str(identity_id)},
        headers=auth_headers_admin,
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)
