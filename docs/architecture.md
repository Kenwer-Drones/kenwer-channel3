# Channel 3 Architecture

## Purpose

Kenwer Channel 3 is the Cognitive Identity Layer: it maintains persistent facts, episodic mission memory, and consolidated validated beliefs so past experience can explainably influence future decision support for Channel 1.

Channel 1 plans and validates missions. Channel 2 fuses sensor/context streams. Channel 3 remembers, evaluates, revises (gated), and retrieves approved knowledge.

## End-to-end sequence

```text
Channel 2                    Channel 3                         Channel 1
─────────                    ─────────                         ─────────
facts/events/expectations ─► Persistent + Episodic Memory
finalize episode ──────────► Celery C1…C6 (proposals only)
                             Knowledge validation
reviewer APPROVE ──────────► BeliefPublicationService
                             Consolidated Memory (approved)
cognitive-context ◄────────────────────────────────────────── retrieve
(no flight commands)
```

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
    consolidated/ B3 (read + gated publication)
  learning/      Belief Revision Engine (C1–C6)
  validation/    Knowledge validation gate
  retrieval/     Channel 1 cognitive retrieval engine
  llm/           Reasoning providers (mock default)
  mcp/           MCP tools (official Python MCP SDK)
  observability/ Agent Beacon adapter (optional, disabled by default)
  workers/       Celery
  storage/       Optional MinIO/S3 client
```

## Final architecture diagram

```text
                 ┌──────────────────────────────────────────────┐
                 │              Kenwer Channel 3                │
                 │           (Cognitive Identity Layer)         │
                 └──────────────────────────────────────────────┘
                    │ REST (/v1/...)          │ MCP (stdio)
                    ▼                         ▼
              ┌───────────┐            ┌────────────┐
              │  FastAPI  │◄───────────│ MCPServer  │
              └─────┬─────┘            └─────┬──────┘
                    │ shared services         │
        ┌───────────┼────────────┬────────────┼──────────┐
        ▼           ▼            ▼            ▼          ▼
   Persistent   Episodic   Consolidated   Learning    Retrieval
     Memory      Memory      Memory        C1–C6       Engine
        │           │            ▲            │
        └───────────┴────────────┴────────────┘
                         │
              ┌──────────┼──────────┐
              ▼          ▼          ▼
         PostgreSQL    Redis     MinIO
         +pgvector    (Celery)  (optional)
                         │
                    Celery worker
                    (Docker)
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

Publication is transactional and exclusive to `BeliefPublicationService` after knowledge validation + authorized human review. Prior versions are superseded or rolled back without deletion. Belief versions record `confidence_basis` and optional `evidence_support_label`; any numeric `confidence` from a model is labeled uncalibrated.

## AuthZ model

Bearer API keys with roles:

- `channel2_ingest` — events, facts, episode lifecycle
- `channel1_retrieve` — approved beliefs / later context retrieve
- `reviewer` — belief proposal review / rollback
- `admin` — all of the above

Credentials bind to a tenant (and optionally a cognitive identity). Cross-tenant access is denied.

## Learning pipeline

Finalized (or evaluable) episodes enqueue Celery jobs:

1. **C1** Episode reconstruction (chronological events, evidence refs, missing vs negative)
2. **C2** Immutable expectation retrieval (reject if no sealed baseline)
3. **C3** Deterministic metric evaluation → `discrepancy_reports` (no causation, no beliefs)
4. **C4** Evidence context (episodic + persistent + approved consolidated) → competing attribution hypotheses (`attribution_hypotheses`). Correlation ≠ causation; abstention allowed. Scores are uncalibrated + labeled.
5. **C5** Candidate lessons (`candidate_lessons`) — hypothesis-only; **never** written to Persistent or Consolidated memory.
6. **C6** Belief reconciliation → `belief_proposals` (`CREATE|REINFORCE|REFINE|REVISE|CHALLENGE`, plus `INSUFFICIENT_EVIDENCE|REDUNDANT|INCONCLUSIVE`). **Never publishes.**

**Publication path (separate from the learning job):** knowledge validation (fail-closed) → authorized human `APPROVE|REJECT|DEFER` → transactional publish / supersede / rollback via `BeliefPublicationService`. Reviewer approval cannot bypass failed validation. Cross-tenant review is denied.

Reasoning uses `ReasoningProvider` (mock by default; optional OpenAI-compatible). Provider failures soft-fail after C3 without corrupting episodic memory. C4–C5 never mutate published beliefs.

**Untrusted data rule:** mission logs, retrieved docs, and tool results are data, not instructions. Model outputs citing nonexistent evidence ids are rejected.

Similarity policy for C6 candidate matching and Channel 1 ranking is documented in [reconciliation-similarity.md](reconciliation-similarity.md): Jaccard is a temporary lexical fallback; semantic ranking runs only after deterministic tenant/applicability filters; similarity alone never decides agreement or contradiction.

### Review APIs

- `GET /v1/proposals` / `GET /v1/proposals/{id}`
- `POST /v1/proposals/{id}/review` — `APPROVE` | `REJECT` | `DEFER`
- `POST /v1/beliefs/{id}/review` — review latest pending proposal targeting a belief
- `POST /v1/beliefs/{id}/rollback` — restore a prior version (append-only history)

### Channel 1 cognitive retrieval

`POST /v1/retrieve/cognitive-context` authenticates the caller, resolves identity/fleet/site scope, returns active persistent facts + **approved** consolidated belief versions (optional episodic historical context), applies deterministic applicability filters before semantic ranking, and returns structured context with evidence refs, limitations, confidence basis, and `knowledge_version`. Proposed / rejected / superseded / rolled_back beliefs are never returned as active validated knowledge. Explicit `no_applicable_knowledge=true` when empty. Retrieval never issues flight commands.

### MCP tool groups

Entry: `kenwer-c3-mcp` / `python -m kenwer_c3.mcp.server` (stdio). Auth via `KENWER_MCP_API_KEY` (same RBAC as REST).

1. Memory retrieval — `retrieve_cognitive_context`, `list_approved_beliefs`, `list_persistent_facts`
2. Mission evidence — `get_mission_episode`, `list_mission_events`
3. Offline evaluation — `submit_learning_job`, `get_learning_job_status`
4. Knowledge review — `submit_knowledge_review` (reviewer/admin; validation fail-closed; no direct published-memory mutation outside publication service)

No MAVLink / flight-control tools.

### Agent Beacon

Optional and **disabled by default** (`BEACON_ENABLED=false`). Adapter boundary: `kenwer_c3.observability.beacon`. Never authoritative mission memory; mission content redacted; external OTLP deferred.

### Test database isolation

Automated pytest uses `kenwer_c3_test` (see `tests/conftest.py`). Development data lives in `kenwer_c3`. Do not delete synthetic records without inventory/verification (`scripts/inventory_dev_beliefs.py`).

## Observability

OpenTelemetry optional. Agent Beacon optional and disabled by default — never the system of record for mission memory.

## Object storage note

Official `minio/minio` Hub images are no longer available for community pull (source-only distribution). See [infrastructure-minio.md](infrastructure-minio.md) for the verified local substitute pinned by digest.
