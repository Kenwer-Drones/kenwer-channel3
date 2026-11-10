# Kenwer Channel 3 — Cognitive Identity Layer

Channel 3 owns persistent, episodic, and consolidated cognitive memory for Kenwer Drones. It evaluates mission outcomes, generates evidence-linked candidate lessons, revises beliefs through a gated review process, and provides decision-support context to Channel 1.

This is a modular monolith (FastAPI + PostgreSQL/pgvector + Redis/Celery + MinIO). It does **not** control flight hardware or rewrite authoritative safety policies.

## Quick start (Phase 1)

```bash
# From repo root
cp .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # Windows
pip install -e ".[dev]"

# Host ports 5433 (Postgres) and 6380 (Redis) avoid conflicts with other local stacks
docker compose up -d postgres redis
alembic upgrade head
uvicorn kenwer_c3.main:app --reload --port 8000
```

Optional profiles:
- `docker compose --profile storage up -d` — MinIO (requires Docker Hub pull access)
- `docker compose --profile full up --build` — API + Celery worker containers

- Health: `GET http://localhost:8000/health`
- Ready: `GET http://localhost:8000/ready`
- OpenAPI: `http://localhost:8000/docs`

## Tests

```bash
docker compose up -d postgres redis
alembic upgrade head
pytest -q
```

## Phase status

- **Phase 1:** repository, infrastructure, database schema, health/ready — complete (pending review)
- Phases 2–7: memory services, BRE, validation, retrieval, MCP, e2e — not started

See `AGENTS.md` (added in Phase 2) for permanent architectural constraints.
