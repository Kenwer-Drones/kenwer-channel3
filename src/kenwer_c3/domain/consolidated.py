"""Consolidated memory contracts (B3) - published beliefs only for retrieval."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from kenwer_c3.domain.common import DomainModel, SafetyImpact


class BeliefApprovalStatus(str, Enum):
    draft = "draft"
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"
    deferred = "deferred"
    superseded = "superseded"
    rolled_back = "rolled_back"


class BeliefVersionRead(DomainModel):
    id: UUID
    tenant_id: UUID
    belief_id: UUID
    cognitive_identity_id: UUID
    version: int
    statement: str
    value_json: dict[str, Any]
    applicability: dict[str, Any]
    confidence: float
    confidence_basis: str = "uncalibrated_model"
    evidence_support_label: str | None = None
    approval_status: BeliefApprovalStatus
    safety_impact: SafetyImpact
    provenance: dict[str, Any]
    limitations: str | None
    supersedes_version_id: UUID | None
    published_at: datetime | None
    created_at: datetime


class BeliefRead(DomainModel):
    id: UUID
    tenant_id: UUID
    cognitive_identity_id: UUID
    belief_key: str
    current_version_id: UUID | None
    current_version: BeliefVersionRead | None = None
    created_at: datetime
    updated_at: datetime
