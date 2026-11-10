# Kenwer Channel 3 — Cognitive Identity Layer

Channel 3 owns **persistent**, **episodic**, and **consolidated** cognitive memory for Kenwer Drones. It evaluates mission outcomes, generates evidence-linked candidate lessons, revises beliefs through gated human review, and returns decision-support context to Channel 1.

**Modular monolith:** FastAPI + PostgreSQL/pgvector + Redis/Celery + optional MinIO + MCP server.

It does **not** control flight hardware, issue MAVLink, or rewrite authoritative safety / flight policies.

## Status (Phase 7)

| Area | State |
|------|--------|
| Memory stores (B1/B2/B3) | Complete |
| Learning pipeline C1–C6 | Complete (mock LLM default) |
| Validation + human review + publication | Complete |
| Channel 1 cognitive retrieval | Complete |
| MCP tool groups (4) | Complete (official Python MCP SDK) |
| Agent Beacon | Optional adapter only; OTLP export **not** implemented |
| Real drone hardware / production deploy | **Out of scope** |

## Quick start

```bash
cp .env.example .env
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Host ports 5433 (Postgres) / 6380 (Redis) avoid local conflicts
docker compose up -d postgres redis
# Optional object storage
docker compose --profile storage up -d minio
# API + live Celery worker
docker compose --profile full up --build -d api worker

alembic upgrade head
uvicorn kenwer_c3.main:app --reload --port 8000
```

- Health: `GET http://localhost:8000/health`
- Ready: `GET http://localhost:8000/ready`
- OpenAPI: `http://localhost:8000/docs`
- MCP (stdio): `kenwer-c3-mcp` with `KENWER_MCP_API_KEY=...`

## Databases

| Database | Purpose |
|----------|---------|
| `kenwer_c3` | Local development / live Docker worker |
| `kenwer_c3_test` | Automated pytest (isolated; default) |

```bash
python scripts/ensure_test_database.py
$env:DATABASE_URL="postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3_test"
alembic upgrade head
pytest -q
```

## End-to-end (live Docker worker)

```bash
docker compose --profile full up --build -d
python scripts/verify_docker_c1_c6.py
python scripts/e2e_mission1_mission2.py
```

## Documentation

| Doc | Contents |
|-----|----------|
| `AGENTS.md` | Permanent architectural constraints |
| `docs/architecture.md` | System design |
| `docs/data-contracts.md` | Domain/ORM contracts |
| `docs/api-examples.md` | REST examples |
| `docs/mcp.md` | MCP tools |
| `docs/deployment-security.md` | AuthZ, secrets, MinIO caveat |
| `docs/operations.md` | Runbooks |
| `docs/channel1-integration.md` | Retrieval for Channel 1 |
| `docs/channel2-integration.md` | Ingest for Channel 2 |
| `docs/reconciliation-similarity.md` | Jaccard/semantic policy |
| `docs/infrastructure-minio.md` | MinIO image / digest |
| `docs/phase7-final-report.md` | Phase 7 acceptance report |

## Dev API keys (local only)

Configured in `.env` / `.env.example`:

- `dev-admin-key` → admin
- `dev-ingest-key` → channel2_ingest
- `dev-retrieve-key` → channel1_retrieve
- `dev-reviewer-key` → reviewer

## Explicit non-goals

- No flight-control tools (REST or MCP)
- No automatic publication of LLM hypotheses
- No calibrated probability claims from model scores
- No production deployment automation in this repository phase
