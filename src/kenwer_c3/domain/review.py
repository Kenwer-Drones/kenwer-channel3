"""Belief proposal review contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from kenwer_c3.domain.common import DomainModel


class ReviewAction(str, Enum):
    approve = "APPROVE"
    reject = "REJECT"
    defer = "DEFER"


class ProposalReviewRequest(DomainModel):
    action: ReviewAction
    rationale: str | None = None


class ProposalReviewResult(DomainModel):
    proposal_id: UUID
    status: str
    operation: str
    challenge_flagged: bool = False
    resulting_belief_version_id: UUID | None = None
    validation_report: dict[str, Any] | None = None
    reviewed_at: datetime | None = None
    review_rationale: str | None = None
    consolidated_memory_changed: bool = False


class BeliefRollbackRequest(DomainModel):
    to_version: int | None = None
    rationale: str | None = None


class BeliefRollbackResult(DomainModel):
    belief_id: UUID
    rolled_back_version_id: UUID
    restored_from_version: int
    new_version_id: UUID
    new_version: int
    current_version_id: UUID


class BeliefProposalRead(DomainModel):
    id: UUID
    tenant_id: UUID
    learning_job_id: UUID
    candidate_lesson_id: UUID | None = None
    target_belief_id: UUID | None = None
    cognitive_identity_id: UUID | None = None
    operation: str
    proposed_payload: dict[str, Any]
    contradiction_notes: str | None = None
    status: str
    validation_report: dict[str, Any] = {}
    flags_existing_for_review: bool = False
    resulting_belief_version_id: UUID | None = None
    reviewed_at: datetime | None = None
    review_rationale: str | None = None
    created_at: datetime
