"""Persistent memory service - versioned durable facts with provenance."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import CognitiveIdentity, FactStatus, MemoryFamily, PersistentFact
from kenwer_c3.domain.persistent import (
    PersistentFactCreate,
    PersistentFactRead,
    PersistentFactWriteResult,
)
from kenwer_c3.memory.audit import write_audit


class PersistentMemoryService:
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

    def _active_fact(
        self, cognitive_identity_id: uuid.UUID, fact_key: str
    ) -> PersistentFact | None:
        stmt = (
            select(PersistentFact)
            .where(
                PersistentFact.tenant_id == self.principal.tenant_id,
                PersistentFact.cognitive_identity_id == cognitive_identity_id,
                PersistentFact.fact_key == fact_key,
                PersistentFact.status == FactStatus.active,
            )
            .order_by(PersistentFact.version.desc())
            .limit(1)
        )
        return self.session.execute(stmt).scalar_one_or_none()

    def get_fact(self, fact_id: uuid.UUID) -> PersistentFactRead:
        fact = self.session.get(PersistentFact, fact_id)
        if (
            fact is None
            or fact.tenant_id != self.principal.tenant_id
        ):
            raise HTTPException(status_code=404, detail="Persistent fact not found")
        self.principal.assert_identity_access(fact.cognitive_identity_id)
        return PersistentFactRead.model_validate(fact)

    def list_facts(
        self,
        cognitive_identity_id: uuid.UUID,
        *,
        fact_key: str | None = None,
        active_only: bool = True,
    ) -> list[PersistentFactRead]:
        self._get_identity(cognitive_identity_id)
        stmt = select(PersistentFact).where(
            PersistentFact.tenant_id == self.principal.tenant_id,
            PersistentFact.cognitive_identity_id == cognitive_identity_id,
        )
        if fact_key:
            stmt = stmt.where(PersistentFact.fact_key == fact_key)
        if active_only:
            stmt = stmt.where(PersistentFact.status == FactStatus.active)
        stmt = stmt.order_by(PersistentFact.fact_key, PersistentFact.version.desc())
        rows = self.session.execute(stmt).scalars().all()
        return [PersistentFactRead.model_validate(r) for r in rows]

    def upsert_fact(self, payload: PersistentFactCreate) -> PersistentFactWriteResult:
        identity = self._get_identity(payload.cognitive_identity_id)
        active = self._active_fact(identity.id, payload.fact_key)

        if active is None:
            fact = self._insert_version(
                identity_id=identity.id,
                fact_key=payload.fact_key,
                category=payload.category,
                value_json=payload.value_json,
                unit=payload.unit,
                description=payload.description,
                version=1,
                source_system=payload.source_system,
                source_event_id=payload.source_event_id,
                provenance=payload.provenance,
                valid_from=payload.valid_from,
            )
            write_audit(
                self.session,
                tenant_id=self.principal.tenant_id,
                actor_key_id=self.principal.api_key_id,
                action="persistent_fact.create",
                resource_type="persistent_fact",
                resource_id=str(fact.id),
                memory_family=MemoryFamily.persistent,
                details={"fact_key": payload.fact_key, "version": 1},
            )
            return PersistentFactWriteResult(
                fact=PersistentFactRead.model_validate(fact),
                created=True,
                message="Created persistent fact version 1",
            )

        # Identical value - idempotent no-op
        if active.value_json == payload.value_json:
            return PersistentFactWriteResult(
                fact=PersistentFactRead.model_validate(active),
                created=False,
                message="Active fact unchanged (idempotent)",
            )

        # Conflict without controlled update - refuse silent overwrite
        if not payload.allow_controlled_update:
            write_audit(
                self.session,
                tenant_id=self.principal.tenant_id,
                actor_key_id=self.principal.api_key_id,
                action="persistent_fact.contradiction_rejected",
                resource_type="persistent_fact",
                resource_id=str(active.id),
                memory_family=MemoryFamily.persistent,
                details={
                    "fact_key": payload.fact_key,
                    "active_version": active.version,
                    "reason": "observation_or_uncontrolled_write_blocked",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Active persistent fact differs from submitted value. "
                    "Observations cannot silently overwrite authoritative facts. "
                    "Set allow_controlled_update=true for an explicit versioned update."
                ),
            )

        # Controlled update: supersede prior, create new version
        now = datetime.now(timezone.utc)
        active.status = FactStatus.superseded
        active.valid_to = now
        self.session.flush()

        new_fact = self._insert_version(
            identity_id=identity.id,
            fact_key=payload.fact_key,
            category=payload.category,
            value_json=payload.value_json,
            unit=payload.unit,
            description=payload.description,
            version=active.version + 1,
            source_system=payload.source_system,
            source_event_id=payload.source_event_id,
            provenance={
                **payload.provenance,
                "supersedes_fact_id": str(active.id),
                "prior_version": active.version,
            },
            valid_from=payload.valid_from or now,
        )
        write_audit(
            self.session,
            tenant_id=self.principal.tenant_id,
            actor_key_id=self.principal.api_key_id,
            action="persistent_fact.controlled_update",
            resource_type="persistent_fact",
            resource_id=str(new_fact.id),
            memory_family=MemoryFamily.persistent,
            details={
                "fact_key": payload.fact_key,
                "new_version": new_fact.version,
                "superseded_fact_id": str(active.id),
            },
        )
        return PersistentFactWriteResult(
            fact=PersistentFactRead.model_validate(new_fact),
            created=True,
            superseded_fact_id=active.id,
            contradiction_detected=True,
            message=f"Created version {new_fact.version}; prior version superseded",
        )

    def _insert_version(
        self,
        *,
        identity_id: uuid.UUID,
        fact_key: str,
        category: str,
        value_json: dict,
        unit: str | None,
        description: str | None,
        version: int,
        source_system: str,
        source_event_id: str | None,
        provenance: dict,
        valid_from: datetime | None,
    ) -> PersistentFact:
        fact = PersistentFact(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            cognitive_identity_id=identity_id,
            fact_key=fact_key,
            category=category,
            value_json=value_json,
            unit=unit,
            description=description,
            version=version,
            status=FactStatus.active,
            valid_from=valid_from or datetime.now(timezone.utc),
            provenance=provenance,
            source_system=source_system,
            source_event_id=source_event_id,
        )
        self.session.add(fact)
        self.session.flush()
        return fact
