"""Acceptance: C1–C6 must execute through the live Docker Celery worker.

Uses Redis + PostgreSQL. Does NOT fall back to in-process task execution.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

# Force non-eager + development DB for live worker verification
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "false"
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3"
)

from kenwer_c3.settings import get_settings

get_settings.cache_clear()

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from fixtures.synthetic.mission1_thermal import (
    episode_payload,
    expectation_payload,
    finalize_payload,
    observation_events,
)
from kenwer_c3.auth.bootstrap import ensure_dev_identity
from kenwer_c3.db.base import get_engine, get_session_factory
from kenwer_c3.db.models import BeliefProposal, LearningJob, LearningJobStatus
from kenwer_c3.main import create_app
from kenwer_c3.workers.celery_app import celery_app
from kenwer_c3.workers.tasks import run_learning_job


def main() -> int:
    settings = get_settings()
    assert settings.celery_task_always_eager is False

    # Confirm Docker worker answers ping
    ping = celery_app.send_task("kenwer_c3.workers.ping")
    try:
        pong = ping.get(timeout=15)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: Docker Celery worker did not answer ping: {exc}")
        return 1
    if pong != {"status": "pong"}:
        print(f"FAIL: unexpected ping response: {pong}")
        return 1
    print("OK: Docker worker ping")

    inspected = celery_app.control.inspect(timeout=5.0)
    stats = inspected.stats() or {}
    if not stats:
        print("FAIL: no Celery workers visible via inspect.stats()")
        return 1
    print(f"OK: workers={list(stats.keys())}")

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = SessionLocal()
    try:
        ensure_dev_identity(session, settings)
        session.commit()
    finally:
        session.close()

    get_settings.cache_clear()
    get_engine.cache_clear()
    get_session_factory.cache_clear()

    # Setup mission via API with eager disabled — only ingest endpoints
    api = TestClient(create_app())
    headers = {"Authorization": "Bearer dev-admin-key"}
    identity_id = settings.dev_cognitive_identity_id
    ep = api.post(
        "/v1/episodes",
        headers=headers,
        json=episode_payload(identity_id, f"docker-c6-{uuid.uuid4().hex[:8]}"),
    )
    if ep.status_code != 200:
        print("FAIL create episode", ep.text)
        return 1
    episode_id = ep.json()["id"]
    assert (
        api.post(
            f"/v1/episodes/{episode_id}/expectations/freeze",
            headers=headers,
            json=expectation_payload(),
        ).status_code
        == 200
    )
    for evt in observation_events(
        episode_id=episode_id, cognitive_identity_id=identity_id
    ):
        assert (
            api.post("/v1/memory/events", headers=headers, json=evt).status_code == 200
        )
    assert (
        api.post(
            f"/v1/episodes/{episode_id}/finalize",
            headers=headers,
            json=finalize_payload(),
        ).status_code
        == 200
    )

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
            idempotency_key=f"docker-live-{uuid.uuid4().hex}",
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    celery_app.conf.task_always_eager = False
    async_result = run_learning_job.apply_async(args=[str(job_id)])
    print(f"OK: enqueued task_id={async_result.id} job_id={job_id}")

    deadline = time.time() + 90
    finished = None
    while time.time() < deadline:
        session = SessionLocal()
        try:
            row = session.get(LearningJob, job_id)
            if row and row.status in (
                LearningJobStatus.completed,
                LearningJobStatus.failed,
                LearningJobStatus.failed_soft,
            ):
                finished = row
                break
        finally:
            session.close()
        time.sleep(0.5)

    if finished is None:
        print("FAIL: Docker worker did not complete job within timeout (no in-process fallback)")
        return 1

    if finished.status != LearningJobStatus.completed:
        print(f"FAIL: job status={finished.status} error={finished.error_message}")
        return 1

    arts = finished.stage_artifacts or {}
    for stage in ("C1", "C2", "C3", "C4", "C5", "C6"):
        if stage not in arts:
            print(f"FAIL: missing stage artifact {stage}")
            return 1
    if finished.current_stage != "C6_done":
        print(f"FAIL: current_stage={finished.current_stage}")
        return 1
    if arts["C6"].get("publication_performed") is not False:
        print("FAIL: C6 must not publish")
        return 1

    session = SessionLocal()
    try:
        proposals = (
            session.execute(
                select(BeliefProposal).where(BeliefProposal.learning_job_id == job_id)
            )
            .scalars()
            .all()
        )
        if not proposals:
            print("FAIL: no belief proposals from C6")
            return 1
    finally:
        session.close()

    # Confirm the async result was not executed eagerly locally
    if async_result.successful():
        print(f"OK: async result state={async_result.state} result={async_result.result}")

    print("PASS: live Docker worker executed C1–C6 against Redis + PostgreSQL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
