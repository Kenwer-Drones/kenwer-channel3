"""Episodic memory contracts (B2)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from kenwer_c3.domain.common import DomainModel


class EpisodeLifecycleState(str, Enum):
    planned = "planned"
    active = "active"
    finalized = "finalized"
    aborted = "aborted"


class ExpectationSnapshotCreate(DomainModel):
    criteria_json: dict[str, Any]
    metric_baselines: dict[str, Any] = Field(default_factory=dict)
    source_system: str
    source_event_id: str | None = None
    captured_at: datetime


class MissionEpisodeCreate(DomainModel):
    cognitive_identity_id: UUID
    external_mission_id: str = Field(min_length=1, max_length=128)
    mission_type: str = Field(min_length=1, max_length=128)
    objectives: dict[str, Any] = Field(default_factory=dict)
    approved_plan: dict[str, Any] = Field(default_factory=dict)
    site_context: dict[str, Any] = Field(default_factory=dict)
    platform_context: dict[str, Any] = Field(default_factory=dict)
    expectation: ExpectationSnapshotCreate | None = None


class MissionEventIngest(DomainModel):
    episode_id: UUID | None = None
    external_mission_id: str | None = None
    cognitive_identity_id: UUID
    event_idempotency_key: str = Field(min_length=1, max_length=255)
    event_type: str = Field(min_length=1, max_length=128)
    sequence_no: int | None = None
    payload: dict[str, Any]
    provenance: dict[str, Any] = Field(default_factory=dict)
    source_system: str = Field(min_length=1, max_length=128)
    source_quality: dict[str, Any] | None = None
    recorded_at: datetime
    # Observation payloads must never silently mutate persistent facts
    proposed_persistent_fact_key: str | None = None


class EvidenceArtifactCreate(DomainModel):
    artifact_key: str
    media_type: str
    storage_uri: str
    checksum_sha256: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    source_system: str
    recorded_at: datetime


class ExpectationSnapshotRead(DomainModel):
    id: UUID
    tenant_id: UUID
    episode_id: UUID
    cognitive_identity_id: UUID
    criteria_json: dict[str, Any]
    metric_baselines: dict[str, Any]
    source_system: str
    source_event_id: str | None
    captured_at: datetime
    created_at: datetime
    is_sealed: bool


class MissionEventRead(DomainModel):
    id: UUID
    tenant_id: UUID
    episode_id: UUID
    cognitive_identity_id: UUID
    event_idempotency_key: str
    event_type: str
    sequence_no: int | None
    payload: dict[str, Any]
    provenance: dict[str, Any]
    source_system: str
    source_quality: dict[str, Any] | None
    recorded_at: datetime
    ingested_at: datetime


class MissionEpisodeRead(DomainModel):
    id: UUID
    tenant_id: UUID
    cognitive_identity_id: UUID
    external_mission_id: str
    mission_type: str
    objectives: dict[str, Any]
    approved_plan: dict[str, Any]
    site_context: dict[str, Any]
    platform_context: dict[str, Any]
    lifecycle_state: EpisodeLifecycleState
    actual_outcomes: dict[str, Any] | None
    missing_data: dict[str, Any]
    started_at: datetime | None
    finalized_at: datetime | None
    created_at: datetime
    updated_at: datetime
    expectation: ExpectationSnapshotRead | None = None


class MissionEventIngestResult(DomainModel):
    event: MissionEventRead
    created: bool
    message: str


class EpisodeFinalizeRequest(DomainModel):
    actual_outcomes: dict[str, Any] = Field(default_factory=dict)
    missing_data: dict[str, Any] = Field(default_factory=dict)


class FreezeExpectationRequest(DomainModel):
    """Seal pre-execution expectations. Content cannot change afterward."""

    criteria_json: dict[str, Any]
    metric_baselines: dict[str, Any] = Field(default_factory=dict)
    source_system: str
    source_event_id: str | None = None
    captured_at: datetime
