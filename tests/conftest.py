"""Shared pytest fixtures for Channel 3 tests.

Automated tests use the isolated ``kenwer_c3_test`` database by default so
they do not contaminate the development database ``kenwer_c3``.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Isolate automated tests from the development database.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3_test",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("BEACON_ENABLED", "false")
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

from kenwer_c3.auth.bootstrap import ensure_dev_identity  # noqa: E402
from kenwer_c3.db.base import get_engine, get_session_factory  # noqa: E402
from kenwer_c3.db import models  # noqa: E402
from kenwer_c3.main import create_app  # noqa: E402
from kenwer_c3.settings import get_settings  # noqa: E402


def _ensure_test_database() -> None:
    """Create kenwer_c3_test if missing (does not touch kenwer_c3 data)."""
    admin = "postgresql://kenwer:kenwer@localhost:5433/postgres"
    import psycopg

    with psycopg.connect(admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                ("kenwer_c3_test",),
            )
            if cur.fetchone() is None:
                cur.execute('CREATE DATABASE "kenwer_c3_test"')


TEST_DATABASE_URL = (
    "postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3_test"
)


@pytest.fixture(autouse=True)
def _restore_isolated_test_database(request):
    """Keep unit/API tests on kenwer_c3_test even if a live-worker test mutates env."""
    if "test_celery_real_worker" in request.node.nodeid:
        yield
        # Restore isolation for subsequent tests
        os.environ["DATABASE_URL"] = TEST_DATABASE_URL
        os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
        get_settings.cache_clear()
        get_engine.cache_clear()
        get_session_factory.cache_clear()
        return
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    yield


@pytest.fixture(scope="session")
def settings():
    _ensure_test_database()
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    cfg = get_settings()
    # Hard guard: refuse to run the suite against the primary development DB
    if cfg.database_url.rstrip("/").endswith("/kenwer_c3") and os.environ.get(
        "ALLOW_TESTS_ON_DEV_DB"
    ) != "1":
        pytest.exit(
            "Refusing to run tests against development database kenwer_c3. "
            "Use kenwer_c3_test (default) or set ALLOW_TESTS_ON_DEV_DB=1 explicitly.",
            returncode=2,
        )
    return cfg


@pytest.fixture(scope="session")
def engine(settings):
    eng = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()
        row = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).fetchone()
        if row is None:
            pytest.fail(
                "pgvector extension missing - run scripts/ensure_test_database.py "
                "and alembic upgrade head against kenwer_c3_test."
            )
    # Apply migrations to the isolated test DB
    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(cfg, "head")
    return eng


@pytest.fixture
def db_session(engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers_admin() -> dict[str, str]:
    return {"Authorization": "Bearer dev-admin-key"}


@pytest.fixture
def auth_headers_ingest() -> dict[str, str]:
    return {"Authorization": "Bearer dev-ingest-key"}


@pytest.fixture
def auth_headers_retrieve() -> dict[str, str]:
    return {"Authorization": "Bearer dev-retrieve-key"}


@pytest.fixture
def auth_headers_reviewer() -> dict[str, str]:
    return {"Authorization": "Bearer dev-reviewer-key"}


@pytest.fixture
def tenant_and_identity(db_session: Session) -> tuple[models.Tenant, models.CognitiveIdentity]:
    tenant = models.Tenant(
        id=uuid.uuid4(),
        name=f"test-tenant-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(tenant)
    db_session.flush()
    identity = models.CognitiveIdentity(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        slug=f"drone-{uuid.uuid4().hex[:6]}",
        display_name="Test Thermal Drone",
        fleet_id="fleet-a",
        drone_id="drone-1",
        platform_type="quadrotor",
    )
    db_session.add(identity)
    db_session.flush()
    return tenant, identity


@pytest.fixture
def other_tenant_identity(
    db_session: Session,
) -> tuple[models.Tenant, models.CognitiveIdentity]:
    tenant = models.Tenant(
        id=uuid.uuid4(),
        name=f"other-tenant-{uuid.uuid4().hex[:8]}",
    )
    db_session.add(tenant)
    db_session.flush()
    identity = models.CognitiveIdentity(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        slug=f"other-{uuid.uuid4().hex[:6]}",
        display_name="Other Tenant Drone",
    )
    db_session.add(identity)
    db_session.flush()
    return tenant, identity


@pytest.fixture
def seeded_dev_identity(engine) -> tuple[uuid.UUID, uuid.UUID]:
    """Commit-visible seed for API integration tests (TestClient uses real sessions)."""
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        settings = get_settings()
        tenant, identity = ensure_dev_identity(session, settings)
        session.commit()
        return tenant.id, identity.id
    finally:
        session.close()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
