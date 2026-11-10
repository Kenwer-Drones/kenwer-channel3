"""C2 - Expectation Retrieval (immutable baseline only)."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from kenwer_c3.db.models import ExpectationSnapshot, MissionEpisode
from kenwer_c3.domain.learning import ExpectationBaseline


class ExpectationRetrievalService:
    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.session = session
        self.tenant_id = tenant_id

    def retrieve(self, episode_id: uuid.UUID) -> ExpectationBaseline:
        episode = self.session.execute(
            select(MissionEpisode).where(
                MissionEpisode.id == episode_id,
                MissionEpisode.tenant_id == self.tenant_id,
            )
        ).scalar_one_or_none()
        if episode is None:
            raise HTTPException(status_code=404, detail="Episode not found")

        snap = self.session.execute(
            select(ExpectationSnapshot).where(
                ExpectationSnapshot.episode_id == episode_id,
                ExpectationSnapshot.tenant_id == self.tenant_id,
            )
        ).scalar_one_or_none()

        if snap is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "No pre-execution expectation snapshot available. "
                    "Evaluation rejected - baselines are never generated retroactively."
                ),
            )
        if not snap.is_sealed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Expectation snapshot exists but is not sealed/approved",
            )

        return ExpectationBaseline(
            snapshot_id=snap.id,
            episode_id=snap.episode_id,
            criteria_json=dict(snap.criteria_json or {}),
            metric_baselines=dict(snap.metric_baselines or {}),
            mission_plan_version=snap.mission_plan_version,
            approved_plan_snapshot=dict(snap.approved_plan_snapshot or {}),
            source_system=snap.source_system,
            source_event_id=snap.source_event_id,
            captured_at=snap.captured_at,
            is_sealed=snap.is_sealed,
        )
