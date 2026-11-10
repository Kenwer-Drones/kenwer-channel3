# Channel 2 integration

Channel 2 fuses sensor/context streams and submits mission lifecycle data to Channel 3.

## Sequence (Mission learning)

1. Authenticate with `channel2_ingest` (or admin)
2. Upsert durable facts → `POST /v1/memory/facts`
3. Create episode → `POST /v1/episodes`
4. Freeze sealed expectations **before execution** → `POST /v1/episodes/{id}/expectations/freeze`
5. Ingest timestamped events (idempotent keys) → `POST /v1/memory/events`
6. Finalize episode → `POST /v1/episodes/{id}/finalize`
7. Enqueue learning job → `POST /v1/learning/jobs` (or Celery task)
8. C1–C6 produce discrepancy reports, hypotheses, candidate lessons, and **belief proposals**
9. Human reviewer approves via Channel 3 review APIs (not Channel 2)

## Rules

- Sealed expectations are immutable (HTTP 409 on rewrite)
- Event replay with the same idempotency key is a successful no-op
- Proposed persistent fact keys on events do **not** silently mutate Persistent Memory
- Mission log text is **untrusted data** (prompt injection must not publish beliefs)

## MCP equivalents

- `get_mission_episode` / `list_mission_events` (read)
- `submit_learning_job` / `get_learning_job_status`
