"""Unit/integration tests for C1–C3 evaluation (no LLM / no belief publication)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from fixtures.synthetic.mission1_thermal import (
    episode_payload,
    expectation_payload,
    finalize_payload,
    observation_events,
)
from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import DiscrepancyReport, LearningJob, LearningJobStatus
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.learning import LearningJobCreate
from kenwer_c3.learning.c1_reconstruction import EpisodeReconstructionService
from kenwer_c3.learning.c2_expectations import ExpectationRetrievalService
from kenwer_c3.learning.c3_evaluation import OutcomeEvaluationService
from kenwer_c3.learning.jobs import LearningJobService
from kenwer_c3.learning.pipeline import LearningPipeline
from kenwer_c3.memory.episodic.service import EpisodicMemoryService
from kenwer_c3.domain.episodic import (
    FreezeExpectationRequest,
    MissionEpisodeCreate,
    MissionEventIngest,
    EpisodeFinalizeRequest,
)


def _principal(tenant_id) -> Principal:
    return Principal(
        tenant_id=tenant_id,
        role=Role.admin,
        api_key_id=None,
        cognitive_identity_id=None,
        key_prefix="test",
    )


def _seed_mission1(session, tenant, identity):
    svc = EpisodicMemoryService(session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            **{
                **episode_payload(identity.id, f"m1-{uuid.uuid4().hex[:8]}"),
                "cognitive_identity_id": identity.id,
            }
        )
    )
    exp = expectation_payload()
    svc.freeze_expectation(
        ep.id,
        FreezeExpectationRequest(
            criteria_json=exp["criteria_json"],
            metric_baselines=exp["metric_baselines"],
            mission_plan_version=exp["mission_plan_version"],
            source_system=exp["source_system"],
            source_event_id=exp["source_event_id"],
            captured_at=datetime.fromisoformat(exp["captured_at"]),
        ),
    )
    for evt in observation_events(
        episode_id=ep.id, cognitive_identity_id=identity.id
    ):
        svc.ingest_event(MissionEventIngest(**evt))
    fin = finalize_payload()
    svc.finalize_episode(
        ep.id,
        EpisodeFinalizeRequest(
            actual_outcomes=fin["actual_outcomes"],
            missing_data=fin["missing_data"],
        ),
    )
    return ep.id


def test_mission1_image_quality_discrepancy(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission1(db_session, tenant, identity)

    reconstruction = EpisodeReconstructionService(db_session, tenant.id).reconstruct(
        episode_id
    )
    assert reconstruction.data_quality["invented_sensor_readings"] is False
    assert len(reconstruction.events) == 3

    baseline = ExpectationRetrievalService(db_session, tenant.id).retrieve(episode_id)
    assert baseline.is_sealed is True
    assert baseline.mission_plan_version == "plan-thermal-v1"

    evaluation = OutcomeEvaluationService().evaluate(reconstruction, baseline)
    iq = next(r for r in evaluation.reports if r.metric_name == "image_quality")
    assert iq.result_status.value == "fail"
    assert iq.observed_value["value"] == 0.62
    assert iq.expected_value["value"] == 0.85
    assert iq.discrepancy["delta"] == pytest.approx(-0.23)
    assert evaluation.summary["causation_inferred"] is False
    assert evaluation.summary["beliefs_generated"] is False


def test_missing_baseline_rejects_evaluation(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    svc = EpisodicMemoryService(db_session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"nobase-{uuid.uuid4().hex[:8]}",
            mission_type="thermal_inspection",
        )
    )
    with pytest.raises(Exception) as exc:
        ExpectationRetrievalService(db_session, tenant.id).retrieve(ep.id)
    assert "retroactively" in str(exc.value.detail).lower() or exc.value.status_code == 409


def test_out_of_order_and_duplicate_detection(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    svc = EpisodicMemoryService(db_session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"ooo-{uuid.uuid4().hex[:8]}",
            mission_type="thermal_inspection",
            approved_plan={"version": "v1"},
        )
    )
    svc.freeze_expectation(
        ep.id,
        FreezeExpectationRequest(
            criteria_json={"image_quality_min": 0.5},
            metric_baselines={},
            source_system="channel1",
            captured_at=datetime.now(timezone.utc),
        ),
    )
    t0 = datetime.now(timezone.utc)
    # Insert later timestamp first, then earlier - reconstruction sorts chronologically
    for key, seq, offset in [
        ("ooo-b", 2, 20),
        ("ooo-a", 1, 5),
    ]:
        svc.ingest_event(
            MissionEventIngest(
                episode_id=ep.id,
                cognitive_identity_id=identity.id,
                event_idempotency_key=key,
                event_type="observation.image_quality",
                sequence_no=seq,
                payload={"score": 0.7, "unit": "score"},
                source_system="channel2",
                recorded_at=t0 + timedelta(seconds=offset),
            )
        )
    recon = EpisodeReconstructionService(db_session, tenant.id).reconstruct(ep.id)
    assert recon.events[0].event_idempotency_key == "ooo-a"
    assert recon.events[1].event_idempotency_key == "ooo-b"


def test_unsupported_metric_and_invalid_unit(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    svc = EpisodicMemoryService(db_session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"badmetric-{uuid.uuid4().hex[:8]}",
            mission_type="thermal_inspection",
            approved_plan={"version": "v1"},
        )
    )
    svc.freeze_expectation(
        ep.id,
        FreezeExpectationRequest(
            criteria_json={
                "image_quality_min": {"value": 0.8, "unit": "percent"},  # invalid unit
                "metrics": ["image_quality", "made_up_metric"],
            },
            metric_baselines={},
            source_system="channel1",
            captured_at=datetime.now(timezone.utc),
        ),
    )
    svc.ingest_event(
        MissionEventIngest(
            episode_id=ep.id,
            cognitive_identity_id=identity.id,
            event_idempotency_key=f"u-{uuid.uuid4().hex[:6]}",
            event_type="observation.image_quality",
            payload={"score": 0.9, "unit": "score"},
            source_system="channel2",
            recorded_at=datetime.now(timezone.utc),
        )
    )
    recon = EpisodeReconstructionService(db_session, tenant.id).reconstruct(ep.id)
    base = ExpectationRetrievalService(db_session, tenant.id).retrieve(ep.id)
    result = OutcomeEvaluationService().evaluate(recon, base)
    by_name = {r.metric_name: r for r in result.reports}
    assert by_name["made_up_metric"].result_status.value == "unknown"
    assert by_name["image_quality"].discrepancy.get("reason") == "invalid_unit"


def test_incomplete_events_yield_unknown(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    svc = EpisodicMemoryService(db_session, _principal(tenant.id))
    ep = svc.create_episode(
        MissionEpisodeCreate(
            cognitive_identity_id=identity.id,
            external_mission_id=f"incomplete-{uuid.uuid4().hex[:8]}",
            mission_type="thermal_inspection",
            approved_plan={"version": "v1"},
        )
    )
    svc.freeze_expectation(
        ep.id,
        FreezeExpectationRequest(
            criteria_json={"image_quality_min": 0.85},
            metric_baselines={},
            source_system="channel1",
            captured_at=datetime.now(timezone.utc),
        ),
    )
    # No observation events - missing, not invented
    recon = EpisodeReconstructionService(db_session, tenant.id).reconstruct(ep.id)
    base = ExpectationRetrievalService(db_session, tenant.id).retrieve(ep.id)
    result = OutcomeEvaluationService().evaluate(recon, base)
    iq = next(r for r in result.reports if r.metric_name == "image_quality")
    assert iq.result_status.value == "unknown"
    assert iq.missing_evidence.get("observation_event") == "observation.image_quality"
    assert iq.observed_value == {}


def test_pipeline_persists_discrepancy_reports(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission1(db_session, tenant, identity)
    job_svc = LearningJobService(db_session, _principal(tenant.id))
    job = job_svc.create(LearningJobCreate(episode_id=episode_id))
    finished = LearningPipeline(db_session).run(job.id)
    assert finished.status == LearningJobStatus.completed
    assert "C3" in finished.stage_artifacts
    rows = (
        db_session.query(DiscrepancyReport)
        .filter(DiscrepancyReport.learning_job_id == job.id)
        .all()
    )
    assert any(
        r.metric_name == "image_quality" and r.result_status == "fail" for r in rows
    )


def test_duplicate_active_job_rejected(db_session, tenant_and_identity):
    tenant, identity = tenant_and_identity
    episode_id = _seed_mission1(db_session, tenant, identity)
    job_svc = LearningJobService(db_session, _principal(tenant.id))
    first = job_svc.create(LearningJobCreate(episode_id=episode_id))
    assert first.status == LearningJobStatus.queued
    with pytest.raises(Exception) as exc:
        job_svc.create(LearningJobCreate(episode_id=episode_id))
    assert exc.value.status_code == 409
