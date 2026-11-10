"""Belief publication, supersession, and rollback.

Only this module may write approved BeliefVersion rows into consolidated
memory. C4–C5–C6 and LLM providers must never call these writers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    BeliefProposal,
    BeliefProposalOperation,
    BeliefVersion,
    ConfidenceBasis,
    SafetyImpact,
    ValidationAction,
    ValidationDecision,
)
from kenwer_c3.domain.common import Role
from kenwer_c3.validation.knowledge import KnowledgeValidationService


class BeliefPublicationService:
    """Transactional publication gated by validation + authorized human review."""

    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def review_proposal(
        self,
        proposal_id: uuid.UUID,
        *,
        action: ValidationAction,
        rationale: str | None = None,
    ) -> dict[str, Any]:
        if self.principal.role not in (Role.reviewer, Role.admin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Reviewer or admin role required",
            )

        proposal = (
            self.session.query(BeliefProposal)
            .filter(BeliefProposal.id == proposal_id)
            .with_for_update()
            .one_or_none()
        )
        if proposal is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        self.principal.assert_tenant(proposal.tenant_id)
        if proposal.cognitive_identity_id is not None:
            self.principal.assert_identity_access(proposal.cognitive_identity_id)

        if proposal.status in ("approved", "published", "rejected", "deferred"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Proposal already finalized with status={proposal.status}",
            )
        if proposal.status == "abstained":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Abstained proposals cannot be reviewed for publication",
            )

        now = datetime.now(timezone.utc)

        if action == ValidationAction.reject:
            proposal.status = "rejected"
            proposal.reviewer_key_id = self.principal.api_key_id
            proposal.review_rationale = rationale
            proposal.reviewed_at = now
            self.session.flush()
            return self._result(proposal, published_version=None)

        if action == ValidationAction.defer:
            proposal.status = "deferred"
            proposal.reviewer_key_id = self.principal.api_key_id
            proposal.review_rationale = rationale
            proposal.reviewed_at = now
            self.session.flush()
            return self._result(proposal, published_version=None)

        # APPROVE path
        if action != ValidationAction.approve:
            raise HTTPException(status_code=400, detail=f"Unknown action {action}")

        # CHALLENGE: flag only — never replace published content
        if proposal.operation == BeliefProposalOperation.CHALLENGE:
            report = KnowledgeValidationService(
                self.session, proposal.tenant_id
            ).validate_proposal(proposal)
            # For challenge we only require evidence integrity + no authoritative override
            critical = {
                c.name: c.passed
                for c in report.checks
                if c.name in ("evidence_integrity", "no_authoritative_override")
            }
            if not all(critical.values()):
                proposal.validation_report = report.to_dict()
                self.session.flush()
                raise HTTPException(
                    status_code=getattr(
                        status, "HTTP_422_UNPROCESSABLE_CONTENT", 422
                    ),
                    detail={
                        "message": "Challenge validation failed — approval denied",
                        "validation": report.to_dict(),
                    },
                )
            proposal.flags_existing_for_review = True
            proposal.status = "challenge_flagged"
            proposal.reviewer_key_id = self.principal.api_key_id
            proposal.review_rationale = rationale
            proposal.reviewed_at = now
            proposal.validation_report = report.to_dict()
            if proposal.target_belief_id:
                # Annotate belief updated_at only — do not change current_version
                belief = self.session.get(Belief, proposal.target_belief_id)
                if belief is not None:
                    belief.updated_at = now
            self.session.flush()
            return self._result(proposal, published_version=None, challenge_flagged=True)

        # Full validation required — reviewer cannot bypass failures
        report = KnowledgeValidationService(
            self.session, proposal.tenant_id
        ).validate_proposal(proposal)
        proposal.validation_report = report.to_dict()
        if not report.passed:
            self.session.flush()
            raise HTTPException(
                status_code=getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422),
                detail={
                    "message": (
                        "Validation failed — reviewer approval cannot bypass "
                        "failed checks"
                    ),
                    "validation": report.to_dict(),
                },
            )

        version = self._publish_approved(proposal, rationale=rationale, now=now)
        return self._result(proposal, published_version=version)

    def rollback(
        self,
        belief_id: uuid.UUID,
        *,
        to_version: int | None = None,
        rationale: str | None = None,
    ) -> dict[str, Any]:
        """Restore a prior approved version without deleting history."""
        if self.principal.role not in (Role.reviewer, Role.admin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Reviewer or admin role required",
            )

        belief = (
            self.session.query(Belief)
            .filter(Belief.id == belief_id)
            .with_for_update()
            .one_or_none()
        )
        if belief is None:
            raise HTTPException(status_code=404, detail="Belief not found")
        self.principal.assert_tenant(belief.tenant_id)
        self.principal.assert_identity_access(belief.cognitive_identity_id)

        versions = (
            self.session.execute(
                select(BeliefVersion)
                .where(BeliefVersion.belief_id == belief.id)
                .order_by(BeliefVersion.version.asc())
            )
            .scalars()
            .all()
        )
        if not versions:
            raise HTTPException(status_code=404, detail="No versions to roll back")

        current = next(
            (v for v in versions if v.id == belief.current_version_id), None
        )
        if current is None:
            current = max(
                (
                    v
                    for v in versions
                    if v.approval_status == BeliefApprovalStatus.approved
                ),
                key=lambda v: v.version,
                default=None,
            )
        if current is None:
            raise HTTPException(status_code=409, detail="No approved current version")

        if to_version is None:
            # Roll back to previous approved version
            candidates = [
                v
                for v in versions
                if v.version < current.version
                and v.approval_status
                in (
                    BeliefApprovalStatus.approved,
                    BeliefApprovalStatus.superseded,
                    BeliefApprovalStatus.rolled_back,
                )
            ]
            if not candidates:
                raise HTTPException(
                    status_code=409, detail="No prior version available for rollback"
                )
            target = max(candidates, key=lambda v: v.version)
        else:
            target = next((v for v in versions if v.version == to_version), None)
            if target is None:
                raise HTTPException(status_code=404, detail="Target version not found")

        now = datetime.now(timezone.utc)
        # Mark current as rolled_back (history preserved)
        current.approval_status = BeliefApprovalStatus.rolled_back

        # Create a new approved version cloning the target (append-only history)
        new_version_num = max(v.version for v in versions) + 1
        restored = BeliefVersion(
            id=uuid.uuid4(),
            tenant_id=belief.tenant_id,
            belief_id=belief.id,
            cognitive_identity_id=belief.cognitive_identity_id,
            version=new_version_num,
            statement=target.statement,
            value_json=dict(target.value_json or {}),
            applicability=dict(target.applicability or {}),
            confidence=target.confidence,
            confidence_basis=target.confidence_basis,
            evidence_support_label=target.evidence_support_label,
            approval_status=BeliefApprovalStatus.approved,
            safety_impact=target.safety_impact,
            provenance={
                **(target.provenance or {}),
                "rollback_of_version_id": str(current.id),
                "restored_from_version_id": str(target.id),
                "rollback_rationale": rationale,
            },
            limitations=target.limitations,
            supersedes_version_id=current.id,
            published_at=now,
        )
        self.session.add(restored)
        self.session.flush()
        belief.current_version_id = restored.id
        belief.updated_at = now

        self.session.add(
            ValidationDecision(
                id=uuid.uuid4(),
                tenant_id=belief.tenant_id,
                belief_version_id=restored.id,
                belief_proposal_id=None,
                action=ValidationAction.approve,
                reviewer_key_id=self.principal.api_key_id,
                rationale=rationale or f"Rollback to version {target.version}",
                checks_passed={"rollback": True, "from_version": target.version},
            )
        )
        self.session.flush()
        return {
            "belief_id": str(belief.id),
            "rolled_back_version_id": str(current.id),
            "restored_from_version": target.version,
            "new_version_id": str(restored.id),
            "new_version": restored.version,
            "current_version_id": str(belief.current_version_id),
        }

    def _publish_approved(
        self,
        proposal: BeliefProposal,
        *,
        rationale: str | None,
        now: datetime,
    ) -> BeliefVersion:
        payload = dict(proposal.proposed_payload or {})
        identity_id = proposal.cognitive_identity_id
        if identity_id is None:
            raise HTTPException(
                status_code=422, detail="Proposal missing cognitive_identity_id"
            )

        belief_key = str(payload.get("belief_key") or f"proposal.{proposal.id}")
        statement = str(payload.get("statement") or payload.get("claim") or "")
        if not statement:
            raise HTTPException(status_code=422, detail="Proposal missing statement")

        # Lock belief row if updating existing
        belief: Belief | None = None
        if proposal.target_belief_id:
            belief = (
                self.session.query(Belief)
                .filter(Belief.id == proposal.target_belief_id)
                .with_for_update()
                .one_or_none()
            )
            if belief is None:
                raise HTTPException(status_code=404, detail="Target belief not found")
            if belief.tenant_id != proposal.tenant_id:
                raise HTTPException(status_code=403, detail="Cross-tenant belief")
        else:
            # CREATE or race: find-or-create by key
            belief = (
                self.session.query(Belief)
                .filter(
                    Belief.tenant_id == proposal.tenant_id,
                    Belief.cognitive_identity_id == identity_id,
                    Belief.belief_key == belief_key,
                )
                .with_for_update()
                .one_or_none()
            )
            if belief is None:
                belief = Belief(
                    id=uuid.uuid4(),
                    tenant_id=proposal.tenant_id,
                    cognitive_identity_id=identity_id,
                    belief_key=belief_key,
                )
                self.session.add(belief)
                self.session.flush()

        existing_versions = (
            self.session.execute(
                select(BeliefVersion).where(BeliefVersion.belief_id == belief.id)
            )
            .scalars()
            .all()
        )
        next_version = (
            max((v.version for v in existing_versions), default=0) + 1
        )

        prior_approved = next(
            (
                v
                for v in existing_versions
                if v.approval_status == BeliefApprovalStatus.approved
                and v.id == belief.current_version_id
            ),
            None,
        )
        if prior_approved is None:
            prior_approved = next(
                (
                    v
                    for v in sorted(existing_versions, key=lambda x: x.version, reverse=True)
                    if v.approval_status == BeliefApprovalStatus.approved
                ),
                None,
            )

        # Safe supersession: mark prior approved as superseded (history kept)
        if prior_approved is not None and proposal.operation != BeliefProposalOperation.CREATE:
            prior_approved.approval_status = BeliefApprovalStatus.superseded
        elif prior_approved is not None and proposal.operation == BeliefProposalOperation.CREATE:
            # Duplicate create against existing key → supersede
            prior_approved.approval_status = BeliefApprovalStatus.superseded

        try:
            safety = SafetyImpact(payload.get("safety_impact", "operational"))
        except ValueError:
            safety = SafetyImpact.operational

        uncalibrated = payload.get("model_confidence_uncalibrated")
        confidence = float(uncalibrated) if uncalibrated is not None else 0.0
        basis = payload.get("confidence_basis") or ConfidenceBasis.uncalibrated_model.value
        label = payload.get("evidence_support_label")

        version = BeliefVersion(
            id=uuid.uuid4(),
            tenant_id=proposal.tenant_id,
            belief_id=belief.id,
            cognitive_identity_id=identity_id,
            version=next_version,
            statement=statement,
            value_json=dict(payload.get("value_json") or {}),
            applicability=dict(payload.get("applicability") or {}),
            confidence=confidence,
            confidence_basis=str(basis),
            evidence_support_label=str(label) if label else None,
            approval_status=BeliefApprovalStatus.approved,
            safety_impact=safety,
            provenance={
                "proposal_id": str(proposal.id),
                "operation": proposal.operation.value
                if hasattr(proposal.operation, "value")
                else str(proposal.operation),
                "supporting_evidence_ids": payload.get("supporting_evidence_ids") or [],
                "contradictory_evidence_ids": payload.get("contradictory_evidence_ids")
                or [],
                "confidence_basis": basis,
                "evidence_support_label": label,
                "model_confidence_uncalibrated": uncalibrated,
                "note": (
                    "model_confidence_uncalibrated is not a calibrated probability"
                ),
                "candidate_lesson_id": str(proposal.candidate_lesson_id)
                if proposal.candidate_lesson_id
                else None,
                "learning_job_id": str(proposal.learning_job_id),
            },
            limitations=payload.get("limitations"),
            supersedes_version_id=prior_approved.id if prior_approved else None,
            published_at=now,
        )
        self.session.add(version)
        self.session.flush()

        belief.current_version_id = version.id
        belief.updated_at = now

        decision = ValidationDecision(
            id=uuid.uuid4(),
            tenant_id=proposal.tenant_id,
            belief_version_id=version.id,
            belief_proposal_id=proposal.id,
            action=ValidationAction.approve,
            reviewer_key_id=self.principal.api_key_id,
            rationale=rationale,
            checks_passed=proposal.validation_report or {},
        )
        self.session.add(decision)

        proposal.status = "published"
        proposal.reviewer_key_id = self.principal.api_key_id
        proposal.review_rationale = rationale
        proposal.reviewed_at = now
        proposal.resulting_belief_version_id = version.id
        self.session.flush()
        return version

    def _result(
        self,
        proposal: BeliefProposal,
        *,
        published_version: BeliefVersion | None,
        challenge_flagged: bool = False,
    ) -> dict[str, Any]:
        return {
            "proposal_id": str(proposal.id),
            "status": proposal.status,
            "operation": proposal.operation.value
            if hasattr(proposal.operation, "value")
            else str(proposal.operation),
            "challenge_flagged": challenge_flagged or proposal.flags_existing_for_review,
            "resulting_belief_version_id": (
                str(published_version.id) if published_version else None
            ),
            "validation_report": proposal.validation_report,
            "reviewed_at": proposal.reviewed_at,
            "review_rationale": proposal.review_rationale,
            "consolidated_memory_changed": published_version is not None,
        }
