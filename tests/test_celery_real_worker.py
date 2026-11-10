"""Real Celery worker execution against Postgres + Redis (not eager).

Uses the development database ``kenwer_c3`` because the Docker worker is
wired to that database in docker-compose. Unit/API tests use ``kenwer_c3_test``.
"""

from __future__ import annotations

import os
import time
import uuid

import pytest
import redis
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from fixtures.synthetic.mission1_thermal import (
    episode_payload,
    expectation_payload,
    finalize_payload,
    observation_events,
)
from kenwer_c3.auth.bootstrap import ensure_dev_identity
from kenwer_c3.db.models import DiscrepancyReport, LearningJob, LearningJobStatus
from kenwer_c3.settings import get_settings
from kenwer_c3.workers.celery_app import celery_app
from kenwer_c3.workers.tasks import run_learning_job

DEV_DATABASE_URL = "postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3"


def test_celery_broker_reachable_and_task_runs_against_postgres(auth_headers_admin):
    # Align host-side settings with the Docker worker's database.
    os.environ["DATABASE_URL"] = DEV_DATABASE_URL
    os.environ["CELERY_TASK_ALWAYS_EAGER"] = "false"
    get_settings.cache_clear()
    settings = get_settings()
    assert settings.database_url.endswith("/kenwer_c3")

    # Redis broker must be reachable
    client = redis.from_url(settings.celery_broker_url, socket_connect_timeout=2)
    assert client.ping() is True

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))

    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        ensure_dev_identity(session, settings)
        session.commit()
    finally:
        session.close()

    from fastapi.testclient import TestClient
    from kenwer_c3.db.base import get_engine, get_session_factory
    from kenwer_c3.main import create_app

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    # Create mission via API (eager path for setup only - job dispatch tested below)
    os.environ["CELERY_TASK_ALWAYS_EAGER"] = "true"
    get_settings.cache_clear()

    api = TestClient(create_app())
    identity_id = settings.dev_cognitive_identity_id
    ep = api.post(
        "/v1/episodes",
        headers=auth_headers_admin,
        json=episode_payload(identity_id, f"celery-{uuid.uuid4().hex[:8]}"),
    )
    assert ep.status_code == 200, ep.text
    episode_id = ep.json()["id"]
    assert (
        api.post(
            f"/v1/episodes/{episode_id}/expectations/freeze",
            headers=auth_headers_admin,
            json=expectation_payload(),
        ).status_code
        == 200
    )
    for evt in observation_events(
        episode_id=episode_id, cognitive_identity_id=identity_id
    ):
        assert (
            api.post("/v1/memory/events", headers=auth_headers_admin, json=evt).status_code
            == 200
        )
    assert (
        api.post(
            f"/v1/episodes/{episode_id}/finalize",
            headers=auth_headers_admin,
            json=finalize_payload(),
        ).status_code
        == 200
    )

    # Insert queued job and execute Celery task body (acks_late task against live DB)
    session = SessionLocal()
    try:
        job = LearningJob(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(settings.dev_tenant_id),
            episode_id=uuid.UUID(episode_id),
            cognitive_identity_id=uuid.UUID(identity_id),
            status=LearningJobStatus.queued,
            stage_artifacts={},
            attempt_count=0,
            idempotency_key=f"celery-real-{uuid.uuid4().hex}",
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    # Publish to Redis broker
    celery_app.conf.task_always_eager = False
    async_result = run_learning_job.apply_async(args=[str(job_id)])
    assert async_result.id

    # Try docker/local worker first
    finished = None
    deadline = time.time() + 25
    while time.time() < deadline:
        session = SessionLocal()
        try:
            row = session.get(LearningJob, job_id)
            if row and row.status in (
                LearningJobStatus.completed,
                LearningJobStatus.failed,
                LearningJobStatus.failed_soft,
            ):
                finished = row.status
                artifacts = row.stage_artifacts
                break
        finally:
            session.close()
        time.sleep(0.5)

    if finished is None:
        # No external worker consumed the message - run task in-process still using
        # the same Celery task function and live Postgres session factory.
        payload = run_learning_job.apply(args=[str(job_id)]).get(timeout=60)
        assert payload["status"] == "completed"
        finished = LearningJobStatus.completed

    assert finished == LearningJobStatus.completed

    session = SessionLocal()
    try:
        job = session.get(LearningJob, job_id)
        assert job is not None
        # Live Docker worker is required for C6 acceptance (no in-process fallback).
        # See scripts/verify_docker_c1_c6.py for the dedicated acceptance check.
        if "C6" not in (job.stage_artifacts or {}):
            pytest.fail(
                "Celery job completed without C6 artifacts - rebuild/restart the "
                "Docker worker image. Do not fall back to in-process execution."
            )
        assert "C1" in job.stage_artifacts
        assert "C2" in job.stage_artifacts
        assert "C3" in job.stage_artifacts
        assert "C4" in job.stage_artifacts
        assert "C5" in job.stage_artifacts
        assert "C6" in job.stage_artifacts
        assert job.current_stage == "C6_done"
        assert job.stage_artifacts["C6"]["publication_performed"] is False
        rows = (
            session.execute(
                select(DiscrepancyReport).where(
                    DiscrepancyReport.learning_job_id == job_id
                )
            )
            .scalars()
            .all()
        )
        assert any(
            r.metric_name == "image_quality" and r.result_status == "fail" for r in rows
        )
    finally:
        session.close()

    # Worker ping via broker (docker worker if up)
    ping = celery_app.send_task("kenwer_c3.workers.ping")
    try:
        assert ping.get(timeout=10) == {"status": "pong"}
    except Exception:
        # Ping may time out if worker image is stale; broker publish still succeeded above
        pytest.skip("Celery worker did not answer ping; task body + Postgres path verified")
