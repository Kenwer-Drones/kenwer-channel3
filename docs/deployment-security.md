# Deployment & security notes (local / pre-production)

## Authentication

- Bearer API keys with roles: `channel2_ingest`, `channel1_retrieve`, `reviewer`, `admin`
- Tenant (and optional cognitive-identity) scope is taken from the **credential**, never trusted from request bodies alone
- `DEV_API_KEYS` are for local development only - replace before any shared environment

## Authorization highlights

| Action | Roles |
|--------|-------|
| Ingest events/facts/episodes | ingest, admin |
| Retrieve beliefs / cognitive context | retrieve, reviewer, admin |
| Review proposals / rollback | reviewer, admin |
| Submit learning jobs | ingest, admin |

Cross-tenant and out-of-scope identity access returns **403/404**.

## Validation gate

Reviewer `APPROVE` cannot bypass knowledge validation (missing/forged evidence, unknown safety impact, authoritative flight/regulatory override language). Failures return **422**.

## LLM / mission content

- Default provider: deterministic **mock**
- External OpenAI-compatible provider requires `LLM_API_KEY`
- Sending mission evidence externally requires explicit `LLM_ALLOW_EXTERNAL_MISSION_CONTENT=true`
- Model numeric scores are **uncalibrated**; prefer `evidence_support_label`

## MCP

- Same API-key RBAC via `KENWER_MCP_API_KEY`
- No MAVLink / flight-control tools
- Review tools reuse `BeliefPublicationService` (no direct published-memory mutation)

## MinIO (object storage)

- Optional for core memory/learning path
- Community Hub `minio/minio` images are not pullable; local stack uses digest-pinned third-party `cleanstart/minio` - **local-dev only**, not a production security endorsement
- See `docs/infrastructure-minio.md`

## Agent Beacon

- `BEACON_ENABLED=false` by default
- Adapter boundary: `kenwer_c3.observability.beacon`
- OTLP export **not** implemented; must never be required for Channel 3 operation
- Must never store authoritative mission knowledge

## Secrets

Do not log API keys, raw sensitive mission payloads, or MinIO credentials by default.
