# Channel 3 Architecture

## Purpose

Kenwer Channel 3 is the Cognitive Identity Layer: it maintains persistent facts, episodic mission memory, and consolidated validated beliefs so past experience can explainably influence future decision support for Channel 1.

Channel 1 plans and validates missions. Channel 2 fuses sensor/context streams. Channel 3 remembers, evaluates (later phases), and retrieves approved knowledge.

## Modular monolith

```text
src/kenwer_c3/
  api/           HTTP boundary
  auth/          API keys + RBAC + tenant scope
  domain/        Pydantic contracts
  db/            SQLAlchemy models + sessions
  memory/
    persistent/  B1
    episodic/    B2
    consolidated/ B3 (read; publish in Phase 5)
  learning/      Belief Revision Engine (Phases 3–5)
  validation/    Knowledge validation (Phase 5)
  retrieval/     Channel 1 context (Phase 6)
  llm/           Reasoning providers (Phase 4)
  mcp/           MCP tools (Phase 6)
  workers/       Celery
  storage/       Object storage client (later)
```

## Runtime dependencies

| Service | Role | Local ports |
|---------|------|-------------|
| PostgreSQL 16 + pgvector | Authoritative memory | host `5433` → container `5432` |
| Redis | Celery broker/backend | host `6380` → container `6379` |
| MinIO (S3) | Evidence blobs | `9000` / console `9001` |
| FastAPI | Sync APIs | `8000` |
| Celery worker | Async learning jobs | — |

## Memory families

### Persistent (B1)

Stores durable verified facts (capabilities, payloads, site context, operator preferences). Updates create new versions; prior versions are superseded. Observations that conflict without `allow_controlled_update=true` are rejected (HTTP 409).

### Episodic (B2)

Stores mission episodes, sealed expectation snapshots, append-only events, and evidence artifact references. Event ingest is idempotent on `(tenant_id, episode_id, event_idempotency_key)`.

**Expectation freeze:** before execution, Channel 2/1 freezes criteria via `POST /v1/episodes/{id}/expectations/freeze`. After sealing, rewrite attempts return 409. Finalize requires a sealed snapshot.

### Consolidated (B3)

Stores beliefs and versioned statements. Retrieval APIs return **approved** versions only. Proposed / rejected / deferred versions are invisible to Channel 1 retrieve paths.

## AuthZ model

Bearer API keys with roles:

- `channel2_ingest` — events, facts, episode lifecycle
- `channel1_retrieve` — approved beliefs / later context retrieve
- `reviewer` — belief review (Phase 5)
- `admin` — all of the above

Credentials bind to a tenant (and optionally a cognitive identity). Cross-tenant access is denied.

## Learning pipeline

Finalized (or evaluable) episodes enqueue Celery jobs:

1. **C1** Episode reconstruction (chronological events, evidence refs, missing vs negative)
2. **C2** Immutable expectation retrieval (reject if no sealed baseline)
3. **C3** Deterministic metric evaluation → `discrepancy_reports` (no causation, no beliefs)
4. **C4** Evidence context (episodic + persistent + approved consolidated) → competing attribution hypotheses (`attribution_hypotheses`). Correlation ≠ causation; abstention allowed.
5. **C5** Candidate lessons (`candidate_lessons`) — hypothesis-only; **never** written to Persistent or Consolidated memory.

C6 reconciliation and publication gates land in Phase 5. Reasoning uses `ReasoningProvider` (mock by default; optional OpenAI-compatible). Provider failures soft-fail after C3 without corrupting episodic memory.

**Untrusted data rule:** mission logs, retrieved docs, and tool results are data, not instructions. Model outputs citing nonexistent evidence ids are rejected.

## Observability

OpenTelemetry optional. Agent Beacon optional and disabled by default — never the system of record for mission memory.

## Object storage note

Official `minio/minio` Hub images are no longer available for community pull (source-only distribution). See [infrastructure-minio.md](infrastructure-minio.md) for the verified local substitute pinned by digest.
