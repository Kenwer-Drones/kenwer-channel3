"""Consolidated memory read service - approved beliefs only."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import Belief, BeliefApprovalStatus, BeliefVersion, CognitiveIdentity
from kenwer_c3.domain.consolidated import BeliefRead, BeliefVersionRead


class ConsolidatedMemoryService:
    """Read path for consolidated memory.

    Publication / review writes land in Phase 5. This service never returns
    unapproved or superseded versions as authoritative current beliefs.
    """

    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def _get_identity(self, cognitive_identity_id: uuid.UUID) -> CognitiveIdentity:
        self.principal.assert_identity_access(cognitive_identity_id)
        identity = self.session.get(CognitiveIdentity, cognitive_identity_id)
        if identity is None or identity.tenant_id != self.principal.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cognitive identity not found in tenant scope",
            )
        return identity

    def list_approved_beliefs(
        self,
        cognitive_identity_id: uuid.UUID,
        *,
        mission_type: str | None = None,
    ) -> list[BeliefRead]:
        self._get_identity(cognitive_identity_id)
        stmt = (
            select(Belief)
            .options(selectinload(Belief.versions))
            .where(
                Belief.tenant_id == self.principal.tenant_id,
                Belief.cognitive_identity_id == cognitive_identity_id,
            )
        )
        beliefs = self.session.execute(stmt).scalars().all()
        results: list[BeliefRead] = []
        for belief in beliefs:
            approved = self._current_approved_version(belief)
            if approved is None:
                continue
            if mission_type:
                applicability = approved.applicability or {}
                mt = applicability.get("mission_type")
                if mt is not None and mt != mission_type:
                    continue
            results.append(
                BeliefRead(
                    id=belief.id,
                    tenant_id=belief.tenant_id,
                    cognitive_identity_id=belief.cognitive_identity_id,
                    belief_key=belief.belief_key,
                    current_version_id=approved.id,
                    current_version=BeliefVersionRead.model_validate(approved),
                    created_at=belief.created_at,
                    updated_at=belief.updated_at,
                )
            )
        return results

    def get_approved_belief(self, belief_id: uuid.UUID) -> BeliefRead:
        belief = self.session.execute(
            select(Belief)
            .options(selectinload(Belief.versions))
            .where(
                Belief.id == belief_id,
                Belief.tenant_id == self.principal.tenant_id,
            )
        ).scalar_one_or_none()
        if belief is None:
            raise HTTPException(status_code=404, detail="Belief not found")
        self.principal.assert_identity_access(belief.cognitive_identity_id)
        approved = self._current_approved_version(belief)
        if approved is None:
            raise HTTPException(
                status_code=404,
                detail="No approved published version for this belief",
            )
        return BeliefRead(
            id=belief.id,
            tenant_id=belief.tenant_id,
            cognitive_identity_id=belief.cognitive_identity_id,
            belief_key=belief.belief_key,
            current_version_id=approved.id,
            current_version=BeliefVersionRead.model_validate(approved),
            created_at=belief.created_at,
            updated_at=belief.updated_at,
        )

    @staticmethod
    def _current_approved_version(belief: Belief) -> BeliefVersion | None:
        approved = [
            v
            for v in belief.versions
            if v.approval_status == BeliefApprovalStatus.approved
        ]
        if not approved:
            return None
        # Prefer current_version_id if it is approved; else highest version
        if belief.current_version_id:
            for v in approved:
                if v.id == belief.current_version_id:
                    return v
        return max(approved, key=lambda v: v.version)
