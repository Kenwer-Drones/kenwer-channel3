"""Shared domain primitives."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Role(str, Enum):
    channel2_ingest = "channel2_ingest"
    channel1_retrieve = "channel1_retrieve"
    reviewer = "reviewer"
    admin = "admin"


class MemoryFamily(str, Enum):
    persistent = "persistent"
    episodic = "episodic"
    consolidated = "consolidated"


class SafetyImpact(str, Enum):
    none = "none"
    operational = "operational"
    safety_relevant = "safety_relevant"
    policy_adjacent = "policy_adjacent"


class DomainModel(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")


class Provenance(DomainModel):
    source_system: str
    source_event_id: str | None = None
    recorded_at: datetime | None = None
    notes: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class CognitiveIdentityRef(DomainModel):
    tenant_id: UUID
    cognitive_identity_id: UUID
    fleet_id: str | None = None
    drone_id: str | None = None
    platform_type: str | None = None
