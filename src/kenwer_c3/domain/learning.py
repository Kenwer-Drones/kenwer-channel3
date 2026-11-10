"""Learning job and evaluation contracts (Phase 3 - C1–C3 only)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from kenwer_c3.domain.common import DomainModel


class MetricComparison(str, Enum):
    gte = "gte"
    lte = "lte"
    eq = "eq"
    gt = "gt"
    lt = "lt"


class EvaluationResultStatus(str, Enum):
    pass_ = "pass"
    fail = "fail"
    unknown = "unknown"
    not_applicable = "not_applicable"


class LearningJobStatus(str, Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    failed_soft = "failed_soft"


class LearningJobCreate(DomainModel):
    episode_id: UUID
    idempotency_key: str | None = Field(
        default=None,
        description="Optional client key to prevent duplicate job enqueue",
    )


class LearningJobRead(DomainModel):
    id: UUID
    tenant_id: UUID
    episode_id: UUID
    cognitive_identity_id: UUID
    status: LearningJobStatus
    current_stage: str | None
    error_message: str | None
    stage_artifacts: dict[str, Any]
    celery_task_id: str | None
    attempt_count: int
    idempotency_key: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class ReconstructedEvent(DomainModel):
    id: UUID
    event_idempotency_key: str
    event_type: str
    sequence_no: int | None
    payload: dict[str, Any]
    provenance: dict[str, Any]
    source_system: str
    source_quality: dict[str, Any] | None
    recorded_at: datetime
    ingested_at: datetime
    chronological_index: int
    out_of_order: bool = False
    is_duplicate_key: bool = False


class EvidenceRef(DomainModel):
    id: UUID
    artifact_key: str
    media_type: str
    storage_uri: str
    checksum_sha256: str | None
    recorded_at: datetime
    metadata_json: dict[str, Any]


class EpisodeReconstruction(DomainModel):
    episode_id: UUID
    tenant_id: UUID
    cognitive_identity_id: UUID
    external_mission_id: str
    mission_type: str
    lifecycle_state: str
    objectives: dict[str, Any]
    approved_plan: dict[str, Any]
    site_context: dict[str, Any]
    platform_context: dict[str, Any]
    actual_outcomes: dict[str, Any] | None
    missing_data: dict[str, Any]
    events: list[ReconstructedEvent]
    evidence: list[EvidenceRef]
    data_quality: dict[str, Any]
    # Explicit absences - never invent sensor values
    missing_observations: list[str] = Field(default_factory=list)
    negative_observations: list[str] = Field(default_factory=list)


class ExpectationBaseline(DomainModel):
    snapshot_id: UUID
    episode_id: UUID
    criteria_json: dict[str, Any]
    metric_baselines: dict[str, Any]
    mission_plan_version: str | None
    approved_plan_snapshot: dict[str, Any]
    source_system: str
    source_event_id: str | None
    captured_at: datetime
    is_sealed: bool


class MetricDefinition(DomainModel):
    name: str
    unit: str
    comparison: MetricComparison
    # Path into expectation metric_baselines / criteria
    expected_path: str
    # Event type that carries the observation
    observation_event_type: str
    # Path into event payload for observed numeric value
    observed_path: str = "value"
    required: bool = True
    tolerance: float | None = None


class DiscrepancyReportRead(DomainModel):
    id: UUID | None = None
    learning_job_id: UUID | None = None
    episode_id: UUID
    metric_name: str
    expected_value: dict[str, Any]
    observed_value: dict[str, Any]
    discrepancy: dict[str, Any]
    result_status: EvaluationResultStatus
    unit: str | None
    comparison_operator: str | None
    evidence_refs: dict[str, Any]
    measurement_quality: dict[str, Any]
    uncertainty: dict[str, Any]
    missing_evidence: dict[str, Any]


class OutcomeEvaluationResult(DomainModel):
    episode_id: UUID
    expectation_snapshot_id: UUID
    reports: list[DiscrepancyReportRead]
    summary: dict[str, Any]
