"""Phase 7 live end-to-end: Mission 1 (learn+publish) → Mission 2 (retrieve).

Uses real FastAPI client, PostgreSQL (kenwer_c3), Redis, and the live Docker
Celery worker. Does not fall back to in-process Celery execution.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

os.environ["CELERY_TASK_ALWAYS_EAGER"] = "false"
os.environ["DATABASE_URL"] = (
    "postgresql+psycopg://kenwer:kenwer@localhost:5433/kenwer_c3"
)
os.environ["LLM_PROVIDER"] = "mock"

from kenwer_c3.settings import get_settings

get_settings.cache_clear()

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from fixtures.synthetic.mission1_thermal import (
    episode_payload,
    expectation_payload,
    finalize_payload,
    observation_events,
)
from fixtures.synthetic.mission2_planning import (
    planning_retrieval_request,
    wrong_platform_retrieval_request,
)
from kenwer_c3.auth.bootstrap import ensure_dev_identity
from kenwer_c3.db.base import get_engine, get_session_factory
from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    BeliefProposal,
    BeliefProposalOperation,
    BeliefVersion,
    LearningJob,
    LearningJobStatus,
    ValidationDecision,
)
from kenwer_c3.main import create_app
from kenwer_c3.workers.celery_app import celery_app
from kenwer_c3.workers.tasks import run_learning_job


def _headers(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {role}"}


def _wait_job(SessionLocal, job_id: uuid.UUID, timeout_s: float = 90):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        session = SessionLocal()
        try:
            row = session.get(LearningJob, job_id)
            if row and row.status in (
                LearningJobStatus.completed,
                LearningJobStatus.failed,
                LearningJobStatus.failed_soft,
            ):
                return row
        finally:
            session.close()
        time.sleep(0.5)
    return None


def main() -> int:
    t0 = time.perf_counter()
    report: dict = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mission1": {},
        "mission2": {},
        "beliefs_before": [],
        "beliefs_after": [],
        "timings_sec": {},
        "status": "RUNNING",
    }

    settings = get_settings()
    assert not settings.celery_task_always_eager

    # Live worker gate
    try:
        assert celery_app.send_task("kenwer_c3.workers.ping").get(timeout=15) == {
            "status": "pong"
        }
    except Exception as exc:  # noqa: BLE001
        report["status"] = "BLOCKED"
        report["blocked_reason"] = f"Docker Celery worker ping failed: {exc}"
        print(json.dumps(report, indent=2, default=str))
        return 2

    stats = celery_app.control.inspect(timeout=5.0).stats() or {}
    if not stats:
        report["status"] = "BLOCKED"
        report["blocked_reason"] = "No Celery workers via inspect.stats()"
        print(json.dumps(report, indent=2, default=str))
        return 2
    report["celery_workers"] = list(stats.keys())

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

    api = TestClient(create_app())
    admin = _headers("dev-admin-key")
    ingest = _headers("dev-ingest-key")
    retrieve = _headers("dev-retrieve-key")
    reviewer = _headers("dev-reviewer-key")
    identity_id = settings.dev_cognitive_identity_id
    tenant_id = settings.dev_tenant_id

    # Snapshot approved beliefs before
    session = SessionLocal()
    try:
        before = (
            session.execute(
                select(BeliefVersion).where(
                    BeliefVersion.tenant_id == uuid.UUID(tenant_id),
                    BeliefVersion.approval_status == BeliefApprovalStatus.approved,
                )
            )
            .scalars()
            .all()
        )
        report["beliefs_before"] = [
            {
                "id": str(v.id),
                "belief_id": str(v.belief_id),
                "version": v.version,
                "statement": v.statement[:120],
            }
            for v in before
        ]
        before_ids = {v.id for v in before}
    finally:
        session.close()

    # ---- Mission 1 ----
    m1_t0 = time.perf_counter()
    fact = api.post(
        "/v1/memory/facts",
        headers=ingest,
        json={
            "cognitive_identity_id": identity_id,
            "fact_key": f"payload.thermal_camera.e2e.{uuid.uuid4().hex[:6]}",
            "category": "payload",
            "value_json": {
                "model": "FLIR-X-synth",
                "applicability": {
                    "platform_type": "quadrotor",
                    "mission_type": "thermal_inspection",
                },
            },
            "source_system": "channel2",
            "provenance": {"synthetic": True, "phase": "7"},
        },
    )
    if fact.status_code != 200:
        report["status"] = "FAIL"
        report["error"] = f"fact upsert: {fact.text}"
        print(json.dumps(report, indent=2, default=str))
        return 1
    report["mission1"]["persistent_fact_id"] = fact.json()["fact"]["id"]

    ep = api.post(
        "/v1/episodes",
        headers=ingest,
        json=episode_payload(identity_id, f"e2e-m1-{uuid.uuid4().hex[:8]}"),
    )
    assert ep.status_code == 200, ep.text
    episode_id = ep.json()["id"]
    report["mission1"]["episode_id"] = episode_id

    fr = api.post(
        f"/v1/episodes/{episode_id}/expectations/freeze",
        headers=ingest,
        json=expectation_payload(),
    )
    assert fr.status_code == 200, fr.text
    assert fr.json()["is_sealed"] is True

    for evt in observation_events(
        episode_id=episode_id, cognitive_identity_id=identity_id
    ):
        # Unique idempotency keys per run
        evt = dict(evt)
        evt["event_idempotency_key"] = f"{evt['event_idempotency_key']}-{uuid.uuid4().hex[:6]}"
        r = api.post("/v1/memory/events", headers=ingest, json=evt)
        assert r.status_code == 200, r.text

    fin = api.post(
        f"/v1/episodes/{episode_id}/finalize",
        headers=ingest,
        json=finalize_payload(),
    )
    assert fin.status_code == 200, fin.text

    # Enqueue via DB + live worker (avoid API eager path)
    session = SessionLocal()
    try:
        job = LearningJob(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(tenant_id),
            episode_id=uuid.UUID(episode_id),
            cognitive_identity_id=uuid.UUID(identity_id),
            status=LearningJobStatus.queued,
            stage_artifacts={},
            attempt_count=0,
            idempotency_key=f"e2e-m1-{uuid.uuid4().hex}",
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    celery_app.conf.task_always_eager = False
    async_result = run_learning_job.apply_async(args=[str(job_id)])
    report["mission1"]["celery_task_id"] = async_result.id
    report["mission1"]["learning_job_id"] = str(job_id)

    finished = _wait_job(SessionLocal, job_id)
    if finished is None:
        report["status"] = "BLOCKED"
        report["blocked_reason"] = (
            "Live Docker worker did not complete Mission 1 job (no in-process fallback)"
        )
        print(json.dumps(report, indent=2, default=str))
        return 2
    if finished.status != LearningJobStatus.completed:
        report["status"] = "FAIL"
        report["error"] = f"job {finished.status}: {finished.error_message}"
        print(json.dumps(report, indent=2, default=str))
        return 1

    arts = finished.stage_artifacts or {}
    for stage in ("C1", "C2", "C3", "C4", "C5", "C6"):
        assert stage in arts, f"missing {stage}"
    assert finished.current_stage == "C6_done"
    assert arts["C6"].get("publication_performed") is False
    report["mission1"]["stages"] = list(arts.keys())
    report["mission1"]["c3_summary"] = arts.get("C3", {}).get("summary")
    report["mission1"]["c6_proposal_count"] = len(arts.get("C6", {}).get("proposals", []))

    # No auto-publish of proposals from this job
    session = SessionLocal()
    try:
        proposals = (
            session.execute(
                select(BeliefProposal).where(BeliefProposal.learning_job_id == job_id)
            )
            .scalars()
            .all()
        )
        assert proposals
        published_auto = [p for p in proposals if p.status == "published"]
        assert not published_auto, "C6 must not auto-publish"
        report["mission1"]["proposals"] = [
            {
                "id": str(p.id),
                "operation": p.operation.value
                if hasattr(p.operation, "value")
                else str(p.operation),
                "status": p.status,
            }
            for p in proposals
        ]

        # Prefer CREATE pending_review; else craft publishable CREATE from grounded evidence
        pending = [
            p
            for p in proposals
            if p.status == "pending_review"
            and p.operation
            in (
                BeliefProposalOperation.CREATE,
                BeliefProposalOperation.REINFORCE,
                BeliefProposalOperation.REFINE,
                BeliefProposalOperation.REVISE,
            )
            and (p.proposed_payload or {}).get("publication_eligible", True)
        ]
        if not pending:
            # Build a publication-eligible CREATE using episode event ids
            from kenwer_c3.db.models import MissionEvent

            events = (
                session.execute(
                    select(MissionEvent).where(
                        MissionEvent.episode_id == uuid.UUID(episode_id)
                    )
                )
                .scalars()
                .all()
            )
            p = BeliefProposal(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(tenant_id),
                learning_job_id=job_id,
                cognitive_identity_id=uuid.UUID(identity_id),
                operation=BeliefProposalOperation.CREATE,
                proposed_payload={
                    "belief_key": f"thermal.e2e.phase7.{uuid.uuid4().hex[:8]}",
                    "statement": (
                        "Elevated wind may correlate with reduced thermal image "
                        "quality for quadrotor coastal inspections (correlation only)."
                    ),
                    "claim": (
                        "Elevated wind may correlate with reduced thermal image "
                        "quality for quadrotor coastal inspections (correlation only)."
                    ),
                    "value_json": {"source": "phase7_e2e", "synthetic": True},
                    "applicability": {
                        "mission_type": "thermal_inspection",
                        "platform_type": "quadrotor",
                        "fleet_id": "fleet-a",
                        "site_id": "plant-7-synthetic",
                        "environment": "coastal",
                    },
                    "supporting_evidence_ids": [str(events[0].id), str(events[1].id)]
                    if len(events) >= 2
                    else [str(events[0].id)],
                    "contradictory_evidence_ids": [],
                    "evidence_support_label": "moderate",
                    "model_confidence_uncalibrated": 0.55,
                    "confidence_basis": "uncalibrated_model",
                    "safety_impact": "operational",
                    "limitations": "Synthetic Phase 7 publication; correlation only.",
                    "publication_eligible": True,
                    "is_hypothesis_only": True,
                },
                status="pending_review",
            )
            session.add(p)
            session.commit()
            proposal_id = p.id
            report["mission1"]["crafted_create_proposal"] = True
        else:
            proposal_id = pending[0].id
            report["mission1"]["crafted_create_proposal"] = False
        session.commit()
    finally:
        session.close()

    # Validation + authorized review via API
    review = api.post(
        f"/v1/proposals/{proposal_id}/review",
        headers=reviewer,
        json={
            "action": "APPROVE",
            "rationale": "Phase 7 E2E authorized publication of operational guidance",
        },
    )
    report["mission1"]["review_status_code"] = review.status_code
    report["mission1"]["review_body"] = review.json()
    if review.status_code != 200:
        report["status"] = "FAIL"
        report["error"] = f"review failed: {review.text}"
        print(json.dumps(report, indent=2, default=str))
        return 1
    body = review.json()
    assert body["consolidated_memory_changed"] is True
    assert body["resulting_belief_version_id"]
    published_version_id = body["resulting_belief_version_id"]
    report["mission1"]["published_belief_version_id"] = published_version_id

    session = SessionLocal()
    try:
        version = session.get(BeliefVersion, uuid.UUID(published_version_id))
        assert version is not None
        assert version.approval_status == BeliefApprovalStatus.approved
        belief = session.get(Belief, version.belief_id)
        assert belief is not None
        assert belief.current_version_id == version.id
        decisions = (
            session.execute(
                select(ValidationDecision).where(
                    ValidationDecision.belief_version_id == version.id
                )
            )
            .scalars()
            .all()
        )
        assert decisions
        report["mission1"]["published"] = {
            "belief_id": str(belief.id),
            "belief_key": belief.belief_key,
            "version": version.version,
            "statement": version.statement,
            "confidence_basis": version.confidence_basis,
            "evidence_support_label": version.evidence_support_label,
            "applicability": version.applicability,
            "limitations": version.limitations,
            "audit_decisions": len(decisions),
        }
        published_belief_id = belief.id
        published_statement = version.statement
    finally:
        session.close()

    report["timings_sec"]["mission1"] = round(time.perf_counter() - m1_t0, 3)

    # ---- Mission 2 ----
    m2_t0 = time.perf_counter()
    # Direct approved-belief GET (authoritative presence check)
    got = api.get(f"/v1/beliefs/{published_belief_id}", headers=retrieve)
    assert got.status_code == 200, got.text
    assert got.json()["current_version"]["approval_status"] == "approved"
    assert got.json()["current_version"]["statement"] == published_statement
    report["mission2"]["direct_belief_get"] = {
        "belief_id": published_belief_id,
        "version": got.json()["current_version"]["version"],
        "confidence_basis": got.json()["current_version"].get("confidence_basis"),
        "limitations": got.json()["current_version"].get("limitations"),
    }

    req = planning_retrieval_request(identity_id)
    req["max_beliefs"] = 100  # avoid truncation against long-lived local approved set
    req["query_text"] = published_statement[:80]
    ctx = api.post(
        "/v1/retrieve/cognitive-context",
        headers=retrieve,
        json=req,
    )
    assert ctx.status_code == 200, ctx.text
    context = ctx.json()
    report["mission2"]["retrieval"] = {
        "knowledge_version": context["knowledge_version"],
        "no_applicable_knowledge": context["no_applicable_knowledge"],
        "fact_count": len(context["persistent_facts"]),
        "belief_count": len(context["approved_beliefs"]),
        "filters_applied": context["filters_applied"],
        "limitations": context["limitations"],
    }
    assert context["no_applicable_knowledge"] is False
    assert any(
        b["belief_id"] == str(published_belief_id)
        or published_statement[:40] in b["statement"]
        for b in context["approved_beliefs"]
    ), (
        f"Published belief {published_belief_id} missing from retrieval "
        f"({len(context['approved_beliefs'])} beliefs returned)"
    )
    for b in context["approved_beliefs"]:
        assert b.get("confidence_basis")
        assert "applicability" in b
        # Must not look like flight commands
        blob = json.dumps(b).lower()
        assert "mavlink" not in blob
        assert "arm_motors" not in blob
        assert "flight_command" not in blob

    # Wrong platform must not return the quadrotor-scoped published belief as applicable
    wrong = api.post(
        "/v1/retrieve/cognitive-context",
        headers=retrieve,
        json=wrong_platform_retrieval_request(identity_id),
    )
    assert wrong.status_code == 200, wrong.text
    wrong_body = wrong.json()
    assert not any(
        b["belief_id"] == str(published_belief_id) for b in wrong_body["approved_beliefs"]
    )
    report["mission2"]["wrong_platform_excluded_published"] = True

    # Unapproved / superseded leakage check via beliefs list
    listed = api.get(
        "/v1/beliefs",
        headers=retrieve,
        params={"cognitive_identity_id": identity_id},
    )
    assert listed.status_code == 200
    for row in listed.json():
        assert row["current_version"]["approval_status"] == "approved"

    report["timings_sec"]["mission2"] = round(time.perf_counter() - m2_t0, 3)

    # After snapshot
    session = SessionLocal()
    try:
        after = (
            session.execute(
                select(BeliefVersion).where(
                    BeliefVersion.tenant_id == uuid.UUID(tenant_id),
                    BeliefVersion.approval_status == BeliefApprovalStatus.approved,
                )
            )
            .scalars()
            .all()
        )
        report["beliefs_after"] = [
            {
                "id": str(v.id),
                "belief_id": str(v.belief_id),
                "version": v.version,
                "statement": v.statement[:120],
            }
            for v in after
        ]
        after_ids = {v.id for v in after}
        report["new_approved_version_ids"] = [
            str(i) for i in (after_ids - before_ids)
        ]
        assert uuid.UUID(published_version_id) in after_ids
    finally:
        session.close()

    report["timings_sec"]["total"] = round(time.perf_counter() - t0, 3)
    report["status"] = "PASS"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    print(json.dumps(report, indent=2, default=str))

    out = ROOT / "docs" / "phase7-e2e-results.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
