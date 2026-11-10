"""Episodic memory service - missions, immutable expectations, append-only events."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import (
    CognitiveIdentity,
    EpisodeLifecycleState,
    ExpectationSnapshot,
    MemoryFamily,
    MissionEpisode,
    MissionEvent,
)
from kenwer_c3.domain.episodic import (
    EpisodeFinalizeRequest,
    ExpectationSnapshotRead,
    FreezeExpectationRequest,
    MissionEpisodeCreate,
    MissionEpisodeRead,
    MissionEventIngest,
    MissionEventIngestResult,
    MissionEventRead,
)
from kenwer_c3.memory.audit import write_audit


class EpisodicMemoryService:
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

    def _load_episode(self, episode_id: uuid.UUID) -> MissionEpisode:
        stmt = (
            select(MissionEpisode)
            .options(selectinload(MissionEpisode.expectation))
            .where(
                MissionEpisode.id == episode_id,
                MissionEpisode.tenant_id == self.principal.tenant_id,
            )
        )
        episode = self.session.execute(stmt).scalar_one_or_none()
        if episode is None:
            raise HTTPException(status_code=404, detail="Mission episode not found")
        self.principal.assert_identity_access(episode.cognitive_identity_id)
        return episode

    def create_episode(self, payload: MissionEpisodeCreate) -> MissionEpisodeRead:
        identity = self._get_identity(payload.cognitive_identity_id)
        existing = self.session.execute(
            select(MissionEpisode).where(
                MissionEpisode.tenant_id == self.principal.tenant_id,
                MissionEpisode.external_mission_id == payload.external_mission_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Episode with this external_mission_id already exists",
            )

        episode = MissionEpisode(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            cognitive_identity_id=identity.id,
            external_mission_id=payload.external_mission_id,
            mission_type=payload.mission_type,
            objectives=payload.objectives,
            approved_plan=payload.approved_plan,
            site_context=payload.site_context,
            platform_context=payload.platform_context,
            lifecycle_state=EpisodeLifecycleState.planned,
        )
        self.session.add(episode)
        self.session.flush()

        if payload.expectation is not None:
            self._create_sealed_expectation(
                episode,
                FreezeExpectationRequest(
                    criteria_json=payload.expectation.criteria_json,
                    metric_baselines=payload.expectation.metric_baselines,
                    mission_plan_version=payload.expectation.mission_plan_version,
                    source_system=payload.expectation.source_system,
                    source_event_id=payload.expectation.source_event_id,
                    captured_at=payload.expectation.captured_at,
                ),
            )

        write_audit(
            self.session,
            tenant_id=self.principal.tenant_id,
            actor_key_id=self.principal.api_key_id,
            action="episode.create",
            resource_type="mission_episode",
            resource_id=str(episode.id),
            memory_family=MemoryFamily.episodic,
            details={"external_mission_id": payload.external_mission_id},
        )
        return self.get_episode(episode.id)

    def get_episode(self, episode_id: uuid.UUID) -> MissionEpisodeRead:
        episode = self._load_episode(episode_id)
        return self._to_read(episode)

    def freeze_expectation(
        self, episode_id: uuid.UUID, payload: FreezeExpectationRequest
    ) -> ExpectationSnapshotRead:
        """Freeze pre-mission expectations before execution.

        Once sealed, criteria cannot be changed (enforced here + unique constraint).
        """
        episode = self._load_episode(episode_id)
        if episode.lifecycle_state in (
            EpisodeLifecycleState.finalized,
            EpisodeLifecycleState.aborted,
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot freeze expectations on a closed episode",
            )
        if episode.expectation is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Expectation snapshot already sealed for this episode. "
                    "Pre-mission criteria are immutable after freeze."
                ),
            )
        snap = self._create_sealed_expectation(episode, payload)
        if episode.lifecycle_state == EpisodeLifecycleState.planned:
            episode.lifecycle_state = EpisodeLifecycleState.active
            episode.started_at = episode.started_at or datetime.now(timezone.utc)
        self.session.flush()
        write_audit(
            self.session,
            tenant_id=self.principal.tenant_id,
            actor_key_id=self.principal.api_key_id,
            action="expectation.freeze",
            resource_type="expectation_snapshot",
            resource_id=str(snap.id),
            memory_family=MemoryFamily.episodic,
            details={"episode_id": str(episode.id), "is_sealed": True},
        )
        return ExpectationSnapshotRead.model_validate(snap)

    def update_expectation_forbidden(
        self, episode_id: uuid.UUID, payload: FreezeExpectationRequest
    ) -> None:
        """Explicit rejection path for attempts to rewrite sealed expectations."""
        episode = self._load_episode(episode_id)
        if episode.expectation is not None and episode.expectation.is_sealed:
            write_audit(
                self.session,
                tenant_id=self.principal.tenant_id,
                actor_key_id=self.principal.api_key_id,
                action="expectation.rewrite_rejected",
                resource_type="expectation_snapshot",
                resource_id=str(episode.expectation.id),
                memory_family=MemoryFamily.episodic,
                details={"episode_id": str(episode.id)},
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Expectation snapshot is sealed and immutable after pre-execution "
                    "approval. Retroactive changes to success criteria are forbidden."
                ),
            )
        # No snapshot yet - treat as freeze
        self.freeze_expectation(episode_id, payload)

    def ingest_event(self, payload: MissionEventIngest) -> MissionEventIngestResult:
        episode = self._resolve_episode_for_event(payload)
        if episode.lifecycle_state == EpisodeLifecycleState.finalized:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot append events to a finalized episode",
            )
        if episode.lifecycle_state == EpisodeLifecycleState.aborted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot append events to an aborted episode",
            )

        # Execution observations require a sealed pre-mission expectation
        if payload.event_type.startswith(("observation.", "telemetry.")):
            if episode.expectation is None or not episode.expectation.is_sealed:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "Cannot record execution observations before freezing "
                        "an approved pre-mission expectation snapshot"
                    ),
                )

        # Observations proposing persistent overwrites are recorded but never applied here
        provenance = dict(payload.provenance)
        if payload.proposed_persistent_fact_key:
            provenance["proposed_persistent_fact_key"] = payload.proposed_persistent_fact_key
            provenance["persistent_write_deferred"] = True
            provenance["note"] = (
                "Observation must not silently overwrite authoritative persistent facts"
            )

        existing = self.session.execute(
            select(MissionEvent).where(
                MissionEvent.tenant_id == self.principal.tenant_id,
                MissionEvent.episode_id == episode.id,
                MissionEvent.event_idempotency_key == payload.event_idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is not None:
            return MissionEventIngestResult(
                event=MissionEventRead.model_validate(existing),
                created=False,
                message="Duplicate event ignored (idempotent)",
            )

        event = MissionEvent(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            episode_id=episode.id,
            cognitive_identity_id=episode.cognitive_identity_id,
            event_idempotency_key=payload.event_idempotency_key,
            event_type=payload.event_type,
            sequence_no=payload.sequence_no,
            payload=payload.payload,
            provenance=provenance,
            source_system=payload.source_system,
            source_quality=payload.source_quality,
            recorded_at=payload.recorded_at,
        )
        try:
            with self.session.begin_nested():
                self.session.add(event)
                self.session.flush()
        except IntegrityError as exc:
            existing = self.session.execute(
                select(MissionEvent).where(
                    MissionEvent.tenant_id == self.principal.tenant_id,
                    MissionEvent.episode_id == episode.id,
                    MissionEvent.event_idempotency_key == payload.event_idempotency_key,
                )
            ).scalar_one_or_none()
            if existing:
                return MissionEventIngestResult(
                    event=MissionEventRead.model_validate(existing),
                    created=False,
                    message="Duplicate event ignored (idempotent)",
                )
            raise HTTPException(status_code=409, detail="Event conflict") from exc

        if episode.lifecycle_state == EpisodeLifecycleState.planned:
            episode.lifecycle_state = EpisodeLifecycleState.active
            episode.started_at = episode.started_at or datetime.now(timezone.utc)

        write_audit(
            self.session,
            tenant_id=self.principal.tenant_id,
            actor_key_id=self.principal.api_key_id,
            action="event.ingest",
            resource_type="mission_event",
            resource_id=str(event.id),
            memory_family=MemoryFamily.episodic,
            details={
                "event_type": payload.event_type,
                "idempotency_key": payload.event_idempotency_key,
            },
        )
        return MissionEventIngestResult(
            event=MissionEventRead.model_validate(event),
            created=True,
            message="Event appended",
        )

    def finalize_episode(
        self, episode_id: uuid.UUID, payload: EpisodeFinalizeRequest
    ) -> MissionEpisodeRead:
        episode = self._load_episode(episode_id)
        if episode.lifecycle_state == EpisodeLifecycleState.finalized:
            return self._to_read(episode)
        if episode.expectation is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Cannot finalize episode without a sealed pre-mission expectation "
                    "snapshot"
                ),
            )
        episode.lifecycle_state = EpisodeLifecycleState.finalized
        episode.actual_outcomes = payload.actual_outcomes
        episode.missing_data = payload.missing_data
        episode.finalized_at = datetime.now(timezone.utc)
        self.session.flush()
        write_audit(
            self.session,
            tenant_id=self.principal.tenant_id,
            actor_key_id=self.principal.api_key_id,
            action="episode.finalize",
            resource_type="mission_episode",
            resource_id=str(episode.id),
            memory_family=MemoryFamily.episodic,
            details={"missing_data_keys": list(payload.missing_data.keys())},
        )
        return self._to_read(episode)

    def list_events(self, episode_id: uuid.UUID) -> list[MissionEventRead]:
        episode = self._load_episode(episode_id)
        rows = (
            self.session.execute(
                select(MissionEvent)
                .where(
                    MissionEvent.tenant_id == self.principal.tenant_id,
                    MissionEvent.episode_id == episode.id,
                )
                .order_by(MissionEvent.recorded_at.asc())
            )
            .scalars()
            .all()
        )
        return [MissionEventRead.model_validate(r) for r in rows]

    def _create_sealed_expectation(
        self, episode: MissionEpisode, payload: FreezeExpectationRequest
    ) -> ExpectationSnapshot:
        plan_version = payload.mission_plan_version or (
            (episode.approved_plan or {}).get("version")
            if isinstance(episode.approved_plan, dict)
            else None
        )
        snap = ExpectationSnapshot(
            id=uuid.uuid4(),
            tenant_id=self.principal.tenant_id,
            episode_id=episode.id,
            cognitive_identity_id=episode.cognitive_identity_id,
            criteria_json=payload.criteria_json,
            metric_baselines=payload.metric_baselines,
            mission_plan_version=str(plan_version) if plan_version is not None else None,
            approved_plan_snapshot=dict(episode.approved_plan or {}),
            source_system=payload.source_system,
            source_event_id=payload.source_event_id,
            captured_at=payload.captured_at,
            is_sealed=True,
        )
        self.session.add(snap)
        self.session.flush()
        # Refresh relationship
        episode.expectation = snap
        return snap

    def _resolve_episode_for_event(self, payload: MissionEventIngest) -> MissionEpisode:
        if payload.episode_id is not None:
            return self._load_episode(payload.episode_id)
        if not payload.external_mission_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Provide episode_id or external_mission_id",
            )
        self._get_identity(payload.cognitive_identity_id)
        episode = self.session.execute(
            select(MissionEpisode)
            .options(selectinload(MissionEpisode.expectation))
            .where(
                MissionEpisode.tenant_id == self.principal.tenant_id,
                MissionEpisode.external_mission_id == payload.external_mission_id,
            )
        ).scalar_one_or_none()
        if episode is None:
            raise HTTPException(status_code=404, detail="Mission episode not found")
        self.principal.assert_identity_access(episode.cognitive_identity_id)
        return episode

    def _to_read(self, episode: MissionEpisode) -> MissionEpisodeRead:
        data = MissionEpisodeRead.model_validate(episode)
        if episode.expectation is not None:
            data = data.model_copy(
                update={
                    "expectation": ExpectationSnapshotRead.model_validate(
                        episode.expectation
                    )
                }
            )
        return data
