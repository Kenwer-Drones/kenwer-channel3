"""Liveness and readiness endpoints."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from kenwer_c3.db.base import get_engine
from kenwer_c3.settings import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Process liveness - does not check dependencies."""
    settings = get_settings()
    return {
        "status": "ok",
        "service": settings.app_name,
        "env": settings.app_env,
    }


@router.get("/ready")
def ready() -> JSONResponse:
    """Readiness - verifies database connectivity (and Redis when configured)."""
    checks: dict[str, str] = {}
    overall_ok = True

    # PostgreSQL
    try:
        engine = get_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            # Confirm pgvector extension is available after migrations
            row = conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
            ).fetchone()
            checks["postgres"] = "ok"
            checks["pgvector"] = "ok" if row else "missing"
            if not row:
                overall_ok = False
    except Exception as exc:  # noqa: BLE001 - surface dependency failure
        checks["postgres"] = f"error: {type(exc).__name__}"
        checks["pgvector"] = "unknown"
        overall_ok = False

    # Redis (optional for Phase 1 readiness; reported but soft)
    settings = get_settings()
    try:
        import redis

        client = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        client.ping()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {type(exc).__name__}"
        # Redis is required for learning jobs; treat as readiness failure
        overall_ok = False

    body = {
        "status": "ready" if overall_ok else "not_ready",
        "checks": checks,
    }
    return JSONResponse(content=body, status_code=200 if overall_ok else 503)
