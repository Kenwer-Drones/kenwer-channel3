# API examples (local)

Base URL: `http://localhost:8000`  
Auth: `Authorization: Bearer <dev-*-key>`

## Channel 2 - ingest

### Upsert persistent fact

```http
POST /v1/memory/facts
Authorization: Bearer dev-ingest-key
Content-Type: application/json

{
  "cognitive_identity_id": "00000000-0000-4000-8000-000000000010",
  "fact_key": "payload.thermal_camera",
  "category": "payload",
  "value_json": {"model": "FLIR-X", "applicability": {"platform_type": "quadrotor"}},
  "source_system": "channel2"
}
```

### Create episode → freeze expectations → events → finalize

```http
POST /v1/episodes
Authorization: Bearer dev-ingest-key

{
  "cognitive_identity_id": "00000000-0000-4000-8000-000000000010",
  "external_mission_id": "m-demo-001",
  "mission_type": "thermal_inspection",
  "approved_plan": {"version": "plan-thermal-v1"},
  "site_context": {"site_id": "plant-7-synthetic"},
  "platform_context": {"platform": "quadrotor"}
}
```

```http
POST /v1/episodes/{episode_id}/expectations/freeze
Authorization: Bearer dev-ingest-key

{
  "criteria_json": {"image_quality_min": 0.85},
  "metric_baselines": {"image_quality": 0.9},
  "source_system": "channel1",
  "captured_at": "2026-11-10T00:00:00Z"
}
```

```http
POST /v1/memory/events
Authorization: Bearer dev-ingest-key

{
  "episode_id": "...",
  "cognitive_identity_id": "00000000-0000-4000-8000-000000000010",
  "event_idempotency_key": "obs-wind-1",
  "event_type": "observation.wind",
  "payload": {"wind_mps": 7.2, "synthetic": true},
  "source_system": "channel2",
  "recorded_at": "2026-11-10T00:00:10Z"
}
```

```http
POST /v1/episodes/{episode_id}/finalize
Authorization: Bearer dev-ingest-key

{"actual_outcomes": {"image_quality": 0.62, "wind_mps": 7.2}, "missing_data": {}}
```

### Enqueue learning job (C1–C6)

```http
POST /v1/learning/jobs
Authorization: Bearer dev-admin-key

{"episode_id": "..."}
```

With `CELERY_TASK_ALWAYS_EAGER=false` and a live worker, the job runs asynchronously. Proposals are **never** auto-published.

## Review / publication

```http
GET /v1/proposals?cognitive_identity_id=00000000-0000-4000-8000-000000000010
Authorization: Bearer dev-reviewer-key
```

```http
POST /v1/proposals/{proposal_id}/review
Authorization: Bearer dev-reviewer-key

{"action": "APPROVE", "rationale": "Operational guidance only"}
```

`REJECT` / `DEFER` leave consolidated memory unchanged. Failed validation returns **422** even for reviewers.

## Channel 1 - retrieval

```http
POST /v1/retrieve/cognitive-context
Authorization: Bearer dev-retrieve-key

{
  "cognitive_identity_id": "00000000-0000-4000-8000-000000000010",
  "mission_type": "thermal_inspection",
  "platform_type": "quadrotor",
  "fleet_id": "fleet-a",
  "site_id": "plant-7-synthetic",
  "environment": "coastal",
  "mission_objective": "Plan follow-up inspection",
  "query_text": "wind thermal image quality",
  "requested_memory_types": ["persistent", "consolidated"]
}
```

Response fields of note: `knowledge_version`, `approved_beliefs[].confidence_basis`, `evidence_refs`, `limitations`, `no_applicable_knowledge`, `filters_applied`.

## Health

```http
GET /health
GET /ready
```
