"""Shared pytest fixtures for Phase 1 smoke tests."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

# Ensure settings resolve to local compose defaults before app import
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3",
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("BEACON_ENABLED", "false")

from kenwer_c3.db import models  # noqa: E402
from kenwer_c3.db.base import Base, get_engine, get_session_factory  # noqa: E402
from kenwer_c3.main import create_app  # noqa: E402
from kenwer_c3.settings import get_settings  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    return get_settings()


@pytest.fixture(scope="session")
def engine(settings):
    eng = create_engine(settings.database_url, pool_pre_ping=True, future=True)
    # Fail fast with a clear message if infra is down
    with eng.connect() as conn:
        conn.execute(text("SELECT 1"))
        row = conn.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        ).fetchone()
        if row is None:
            pytest.fail(
                "pgvector extension missing — run `alembic upgrade head` against "
                "the local Postgres from docker compose."
            )
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


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
