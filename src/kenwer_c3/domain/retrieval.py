"""Channel 1 cognitive retrieval contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import Field

from kenwer_c3.domain.common import DomainModel, SafetyImpact


class MemoryTypeRequest(str, Enum):
    persistent = "persistent"
    consolidated = "consolidated"
    episodic = "episodic"


class CognitiveRetrievalRequest(DomainModel):
    cognitive_identity_id: UUID
    mission_objective: str | None = None
    mission_type: str | None = None
    platform_type: str | None = None
    payload_configuration: dict[str, Any] = Field(default_factory=dict)
    site_id: str | None = None
    fleet_id: str | None = None
    environment: str | None = None
    operating_conditions: dict[str, Any] = Field(default_factory=dict)
    requested_memory_types: list[MemoryTypeRequest] = Field(
        default_factory=lambda: [
            MemoryTypeRequest.persistent,
            MemoryTypeRequest.consolidated,
        ]
    )
    include_episodic_limit: int = Field(default=5, ge=0, le=50)
    query_text: str | None = None
    max_beliefs: int = Field(default=20, ge=1, le=100)
    max_facts: int = Field(default=50, ge=1, le=200)


class EvidenceRef(DomainModel):
    evidence_id: UUID | str
    kind: str
    note: str | None = None


class RetrievedFact(DomainModel):
    id: UUID
    fact_key: str
    category: str | None = None
    version: int
    value_json: dict[str, Any]
    provenance: dict[str, Any] = Field(default_factory=dict)
    applicability_score: float | None = None


class RetrievedBelief(DomainModel):
    belief_id: UUID
    belief_key: str
    belief_version_id: UUID
    version: int
    statement: str
    value_json: dict[str, Any]
    applicability: dict[str, Any]
    confidence: float
    confidence_basis: str
    evidence_support_label: str | None = None
    safety_impact: SafetyImpact
    limitations: str | None = None
    published_at: datetime | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    ranking_score: float | None = None
    ranking_method: str | None = None


class RetrievedEpisodeSummary(DomainModel):
    episode_id: UUID
    external_mission_id: str | None = None
    mission_type: str | None = None
    lifecycle_state: str | None = None
    finalized_at: datetime | None = None
    ranking_score: float | None = None


class CognitiveContextResponse(DomainModel):
    tenant_id: UUID
    cognitive_identity_id: UUID
    knowledge_version: str
    no_applicable_knowledge: bool
    scope: dict[str, Any]
    persistent_facts: list[RetrievedFact] = Field(default_factory=list)
    approved_beliefs: list[RetrievedBelief] = Field(default_factory=list)
    episodic_context: list[RetrievedEpisodeSummary] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    filters_applied: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
