"""Phase 7 infrastructure checks."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


def test_migrations_apply_to_fresh_database():
    """Create a throwaway DB, upgrade head, then drop - does not touch kenwer_c3."""
    import psycopg

    from kenwer_c3.settings import get_settings

    name = f"kenwer_c3_mig_{uuid.uuid4().hex[:8]}"
    admin = "postgresql://kenwer:kenwer@localhost:5433/postgres"
    url = f"postgresql+psycopg://kenwer:kenwer@localhost:5433/{name}"
    previous_url = os.environ.get("DATABASE_URL")
    with psycopg.connect(admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{name}"')
    try:
        eng = create_engine(url, pool_pre_ping=True)
        with eng.connect() as c:
            c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            c.commit()
        # alembic/env.py reads get_settings().database_url - set env explicitly
        os.environ["DATABASE_URL"] = url
        get_settings.cache_clear()
        cfg = Config(str(ROOT / "alembic.ini"))
        command.upgrade(cfg, "head")
        with eng.connect() as c:
            ver = c.execute(text("SELECT version_num FROM alembic_version")).scalar()
            assert ver == "20260323_0003"
            tables = {
                r[0]
                for r in c.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                    )
                )
            }
            for required in (
                "beliefs",
                "belief_versions",
                "belief_proposals",
                "learning_jobs",
                "mission_episodes",
            ):
                assert required in tables
        eng.dispose()
    finally:
        if previous_url is not None:
            os.environ["DATABASE_URL"] = previous_url
        else:
            os.environ["DATABASE_URL"] = (
                "postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3_test"
            )
        get_settings.cache_clear()
        with psycopg.connect(admin, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (name,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{name}"')


def test_dockerfile_and_compose_present():
    assert (ROOT / "Dockerfile").is_file()
    assert (ROOT / "docker-compose.yml").is_file()
    text_docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "kenwer_c3" in text_docker or "PYTHONPATH" in text_docker


def test_minio_local_security_limitation_documented():
    doc = (ROOT / "docs" / "infrastructure-minio.md").read_text(encoding="utf-8")
    assert "cleanstart" in doc.lower() or "digest" in doc.lower()
    sec = (ROOT / "docs" / "deployment-security.md").read_text(encoding="utf-8")
    assert "local" in sec.lower()
    assert "minio" in sec.lower()
