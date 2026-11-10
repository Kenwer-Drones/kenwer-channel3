"""Human review and belief publication APIs (Phase 5)."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from kenwer_c3.auth.context import Principal, get_db_session, require_roles
from kenwer_c3.db.models import BeliefProposal, ValidationAction
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.review import (
    BeliefProposalRead,
    BeliefRollbackRequest,
    BeliefRollbackResult,
    ProposalReviewRequest,
    ProposalReviewResult,
    ReviewAction,
)
from kenwer_c3.memory.consolidated.publication import BeliefPublicationService

router = APIRouter(prefix="/v1", tags=["review"])


@router.get(
    "/proposals",
    response_model=list[BeliefProposalRead],
    summary="List belief proposals for a cognitive identity",
)
def list_proposals(
    cognitive_identity_id: UUID = Query(...),
    status_filter: str | None = Query(None, alias="status"),
    principal: Principal = Depends(
        require_roles(Role.reviewer, Role.admin, Role.channel1_retrieve)
    ),
    session: Session = Depends(get_db_session),
) -> list[BeliefProposalRead]:
    principal.assert_identity_access(cognitive_identity_id)
    q = select(BeliefProposal).where(
        BeliefProposal.tenant_id == principal.tenant_id,
        BeliefProposal.cognitive_identity_id == cognitive_identity_id,
    )
    if status_filter:
        q = q.where(BeliefProposal.status == status_filter)
    rows = session.execute(q.order_by(BeliefProposal.created_at.desc())).scalars().all()
    return [BeliefProposalRead.model_validate(r) for r in rows]


@router.get(
    "/proposals/{proposal_id}",
    response_model=BeliefProposalRead,
    summary="Get a belief proposal",
)
def get_proposal(
    proposal_id: UUID,
    principal: Principal = Depends(
        require_roles(Role.reviewer, Role.admin, Role.channel1_retrieve)
    ),
    session: Session = Depends(get_db_session),
) -> BeliefProposalRead:
    row = session.get(BeliefProposal, proposal_id)
    if row is None or row.tenant_id != principal.tenant_id:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Proposal not found")
    if row.cognitive_identity_id is not None:
        principal.assert_identity_access(row.cognitive_identity_id)
    return BeliefProposalRead.model_validate(row)


@router.post(
    "/proposals/{proposal_id}/review",
    response_model=ProposalReviewResult,
    summary="APPROVE / REJECT / DEFER a belief proposal (authorized reviewer)",
)
def review_proposal(
    proposal_id: UUID,
    body: ProposalReviewRequest,
    principal: Principal = Depends(require_roles(Role.reviewer, Role.admin)),
    session: Session = Depends(get_db_session),
) -> ProposalReviewResult:
    action_map = {
        ReviewAction.approve: ValidationAction.approve,
        ReviewAction.reject: ValidationAction.reject,
        ReviewAction.defer: ValidationAction.defer,
    }
    result = BeliefPublicationService(session, principal).review_proposal(
        proposal_id,
        action=action_map[body.action],
        rationale=body.rationale,
    )
    return ProposalReviewResult(
        proposal_id=UUID(result["proposal_id"]),
        status=result["status"],
        operation=result["operation"],
        challenge_flagged=result.get("challenge_flagged", False),
        resulting_belief_version_id=(
            UUID(result["resulting_belief_version_id"])
            if result.get("resulting_belief_version_id")
            else None
        ),
        validation_report=result.get("validation_report"),
        reviewed_at=result.get("reviewed_at"),
        review_rationale=result.get("review_rationale"),
        consolidated_memory_changed=result.get("consolidated_memory_changed", False),
    )


@router.post(
    "/beliefs/{belief_id}/review",
    response_model=ProposalReviewResult,
    summary="Review the latest pending proposal targeting a belief",
)
def review_belief_latest_proposal(
    belief_id: UUID,
    body: ProposalReviewRequest,
    principal: Principal = Depends(require_roles(Role.reviewer, Role.admin)),
    session: Session = Depends(get_db_session),
) -> ProposalReviewResult:
    row = (
        session.execute(
            select(BeliefProposal)
            .where(
                BeliefProposal.tenant_id == principal.tenant_id,
                BeliefProposal.target_belief_id == belief_id,
                BeliefProposal.status == "pending_review",
            )
            .order_by(BeliefProposal.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if row is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404,
            detail="No pending proposal targeting this belief",
        )
    return review_proposal(row.id, body, principal, session)


@router.post(
    "/beliefs/{belief_id}/rollback",
    response_model=BeliefRollbackResult,
    summary="Roll back to a prior belief version without deleting history",
)
def rollback_belief(
    belief_id: UUID,
    body: BeliefRollbackRequest,
    principal: Principal = Depends(require_roles(Role.reviewer, Role.admin)),
    session: Session = Depends(get_db_session),
) -> BeliefRollbackResult:
    result = BeliefPublicationService(session, principal).rollback(
        belief_id,
        to_version=body.to_version,
        rationale=body.rationale,
    )
    return BeliefRollbackResult(
        belief_id=UUID(result["belief_id"]),
        rolled_back_version_id=UUID(result["rolled_back_version_id"]),
        restored_from_version=result["restored_from_version"],
        new_version_id=UUID(result["new_version_id"]),
        new_version=result["new_version"],
        current_version_id=UUID(result["current_version_id"]),
    )
