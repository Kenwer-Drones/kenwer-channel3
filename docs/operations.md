# Operations runbook (local)

## Start dependencies

```bash
docker compose up -d postgres redis
docker compose --profile storage up -d minio   # optional
docker compose --profile full up --build -d api worker
```

## Migrations

```bash
# Development DB
$env:DATABASE_URL="postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3"
alembic upgrade head

# Isolated test DB
python scripts/ensure_test_database.py
$env:DATABASE_URL="postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3_test"
alembic upgrade head
```

## Celery worker

```bash
# Via compose (recommended for acceptance)
docker compose --profile full logs -f worker

# Host-side (dev)
celery -A kenwer_c3.workers.celery_app worker --loglevel=INFO
```

Ensure `CELERY_TASK_ALWAYS_EAGER=false` for real async execution.

## Acceptance scripts

```bash
python scripts/verify_docker_c1_c6.py
python scripts/e2e_mission1_mission2.py
python scripts/inventory_dev_beliefs.py   # report only — does not delete
```

## Failure modes

| Symptom | Check |
|---------|--------|
| Jobs stuck `queued` | Worker up? Redis reachable? `inspect.stats()` |
| Job completed without `C6` | Rebuild worker image; volume-mounted `src` may be fine but deps need rebuild |
| Review 422 | Validation report in response body |
| Tests hitting `kenwer_c3` | Ensure `DATABASE_URL` points at `kenwer_c3_test` |

## Backups / data

Automated tests must not purge `kenwer_c3` development data. Inventory first (`scripts/inventory_dev_beliefs.py`); delete only with explicit approval.
