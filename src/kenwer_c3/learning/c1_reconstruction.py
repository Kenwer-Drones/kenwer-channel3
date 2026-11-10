"""C1 - Episode Reconstruction."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from kenwer_c3.db.models import EvidenceArtifact, MissionEpisode, MissionEvent
from kenwer_c3.domain.learning import (
    EpisodeReconstruction,
    EvidenceRef,
    ReconstructedEvent,
)


class EpisodeReconstructionService:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.session = session
        self.tenant_id = tenant_id

    def reconstruct(self, episode_id: uuid.UUID) -> EpisodeReconstruction:
        episode = self.session.execute(
            select(MissionEpisode)
            .options(
                selectinload(MissionEpisode.events),
                selectinload(MissionEpisode.evidence_artifacts),
                selectinload(MissionEpisode.expectation),
            )
            .where(
                MissionEpisode.id == episode_id,
                MissionEpisode.tenant_id == self.tenant_id,
            )
        ).scalar_one_or_none()
        if episode is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Episode not found for reconstruction",
            )

        # Deterministic chronological order by recorded_at, then sequence_no, then ingest
        sorted_events = sorted(
            episode.events,
            key=lambda e: (
                e.recorded_at,
                e.sequence_no if e.sequence_no is not None else 10**9,
                e.ingested_at,
                str(e.id),
            ),
        )

        seen_keys: set[str] = set()
        reconstructed: list[ReconstructedEvent] = []
        duplicate_keys: list[str] = []
        out_of_order_count = 0
        prev_recorded = None
        prev_seq = None

        for idx, event in enumerate(sorted_events):
            is_dup = event.event_idempotency_key in seen_keys
            if is_dup:
                duplicate_keys.append(event.event_idempotency_key)
            seen_keys.add(event.event_idempotency_key)

            out_of_order = False
            if prev_recorded is not None and event.recorded_at < prev_recorded:
                out_of_order = True
            if (
                prev_seq is not None
                and event.sequence_no is not None
                and event.sequence_no < prev_seq
            ):
                out_of_order = True
            if out_of_order:
                out_of_order_count += 1

            reconstructed.append(
                ReconstructedEvent(
                    id=event.id,
                    event_idempotency_key=event.event_idempotency_key,
                    event_type=event.event_type,
                    sequence_no=event.sequence_no,
                    payload=dict(event.payload or {}),
                    provenance=dict(event.provenance or {}),
                    source_system=event.source_system,
                    source_quality=event.source_quality,
                    recorded_at=event.recorded_at,
                    ingested_at=event.ingested_at,
                    chronological_index=idx,
                    out_of_order=out_of_order,
                    is_duplicate_key=is_dup,
                )
            )
            prev_recorded = event.recorded_at
            if event.sequence_no is not None:
                prev_seq = event.sequence_no

        evidence = [
            EvidenceRef(
                id=a.id,
                artifact_key=a.artifact_key,
                media_type=a.media_type,
                storage_uri=a.storage_uri,
                checksum_sha256=a.checksum_sha256,
                recorded_at=a.recorded_at,
                metadata_json=dict(a.metadata_json or {}),
            )
            for a in sorted(
                episode.evidence_artifacts, key=lambda x: (x.recorded_at, x.artifact_key)
            )
        ]

        missing_observations: list[str] = []
        negative_observations: list[str] = []
        # Explicit missing_data on episode - absence, not a fabricated reading
        for key, reason in (episode.missing_data or {}).items():
            missing_observations.append(f"{key}:{reason}")

        for event in reconstructed:
            # Negative observation: explicit boolean false / not_detected markers
            payload = event.payload
            if payload.get("detected") is False or payload.get("present") is False:
                negative_observations.append(event.event_type)
            if payload.get("observation_status") == "negative":
                negative_observations.append(event.event_type)
            if payload.get("observation_status") == "missing":
                missing_observations.append(event.event_type)

        data_quality = {
            "event_count": len(reconstructed),
            "duplicate_idempotency_keys": duplicate_keys,
            "out_of_order_count": out_of_order_count,
            "evidence_count": len(evidence),
            "has_sealed_expectation": episode.expectation is not None
            and bool(episode.expectation.is_sealed),
            "invented_sensor_readings": False,
        }

        return EpisodeReconstruction(
            episode_id=episode.id,
            tenant_id=episode.tenant_id,
            cognitive_identity_id=episode.cognitive_identity_id,
            external_mission_id=episode.external_mission_id,
            mission_type=episode.mission_type,
            lifecycle_state=episode.lifecycle_state.value
            if hasattr(episode.lifecycle_state, "value")
            else str(episode.lifecycle_state),
            objectives=dict(episode.objectives or {}),
            approved_plan=dict(episode.approved_plan or {}),
            site_context=dict(episode.site_context or {}),
            platform_context=dict(episode.platform_context or {}),
            actual_outcomes=episode.actual_outcomes,
            missing_data=dict(episode.missing_data or {}),
            events=reconstructed,
            evidence=evidence,
            data_quality=data_quality,
            missing_observations=missing_observations,
            negative_observations=negative_observations,
        )
