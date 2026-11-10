"""Memory ingestion and read APIs for Channel 3."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from kenwer_c3.auth.context import Principal, get_db_session, require_roles
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.consolidated import BeliefRead
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
from kenwer_c3.domain.persistent import (
    PersistentFactCreate,
    PersistentFactRead,
    PersistentFactWriteResult,
)
from kenwer_c3.memory.consolidated import ConsolidatedMemoryService
from kenwer_c3.memory.episodic import EpisodicMemoryService
from kenwer_c3.memory.persistent import PersistentMemoryService

router = APIRouter(prefix="/v1", tags=["memory"])


@router.post(
    "/memory/events",
    response_model=MissionEventIngestResult,
    summary="Ingest an episodic mission event (idempotent)",
)
def ingest_event(
    body: MissionEventIngest,
    principal: Principal = Depends(
        require_roles(Role.channel2_ingest, Role.admin)
    ),
    session: Session = Depends(get_db_session),
) -> MissionEventIngestResult:
    return EpisodicMemoryService(session, principal).ingest_event(body)


@router.post(
    "/memory/facts",
    response_model=PersistentFactWriteResult,
    summary="Create or controlled-update a persistent fact",
)
def upsert_fact(
    body: PersistentFactCreate,
    principal: Principal = Depends(
        require_roles(Role.channel2_ingest, Role.admin)
    ),
    session: Session = Depends(get_db_session),
) -> PersistentFactWriteResult:
    return PersistentMemoryService(session, principal).upsert_fact(body)


@router.get(
    "/memory/facts",
    response_model=list[PersistentFactRead],
    summary="List persistent facts for a cognitive identity",
)
def list_facts(
    cognitive_identity_id: UUID = Query(...),
    fact_key: str | None = None,
    active_only: bool = True,
    principal: Principal = Depends(
        require_roles(
            Role.channel2_ingest,
            Role.channel1_retrieve,
            Role.reviewer,
            Role.admin,
        )
    ),
    session: Session = Depends(get_db_session),
) -> list[PersistentFactRead]:
    return PersistentMemoryService(session, principal).list_facts(
        cognitive_identity_id, fact_key=fact_key, active_only=active_only
    )


@router.post(
    "/episodes",
    response_model=MissionEpisodeRead,
    summary="Create a mission episode",
)
def create_episode(
    body: MissionEpisodeCreate,
    principal: Principal = Depends(
        require_roles(Role.channel2_ingest, Role.admin)
    ),
    session: Session = Depends(get_db_session),
) -> MissionEpisodeRead:
    return EpisodicMemoryService(session, principal).create_episode(body)


@router.get(
    "/episodes/{episode_id}",
    response_model=MissionEpisodeRead,
    summary="Get a mission episode",
)
def get_episode(
    episode_id: UUID,
    principal: Principal = Depends(
        require_roles(
            Role.channel2_ingest,
            Role.channel1_retrieve,
            Role.reviewer,
            Role.admin,
        )
    ),
    session: Session = Depends(get_db_session),
) -> MissionEpisodeRead:
    return EpisodicMemoryService(session, principal).get_episode(episode_id)


@router.post(
    "/episodes/{episode_id}/expectations/freeze",
    response_model=ExpectationSnapshotRead,
    summary="Freeze immutable pre-mission expectations before execution",
)
def freeze_expectation(
    episode_id: UUID,
    body: FreezeExpectationRequest,
    principal: Principal = Depends(
        require_roles(Role.channel2_ingest, Role.admin)
    ),
    session: Session = Depends(get_db_session),
) -> ExpectationSnapshotRead:
    return EpisodicMemoryService(session, principal).freeze_expectation(
        episode_id, body
    )


@router.put(
    "/episodes/{episode_id}/expectations",
    response_model=ExpectationSnapshotRead,
    summary="Attempt to set expectations (rejects rewrite of sealed snapshots)",
)
def put_expectation(
    episode_id: UUID,
    body: FreezeExpectationRequest,
    principal: Principal = Depends(
        require_roles(Role.channel2_ingest, Role.admin)
    ),
    session: Session = Depends(get_db_session),
) -> ExpectationSnapshotRead:
    svc = EpisodicMemoryService(session, principal)
    svc.update_expectation_forbidden(episode_id, body)
    episode = svc.get_episode(episode_id)
    assert episode.expectation is not None
    return episode.expectation


@router.post(
    "/episodes/{episode_id}/finalize",
    response_model=MissionEpisodeRead,
    summary="Finalize a mission episode",
)
def finalize_episode(
    episode_id: UUID,
    body: EpisodeFinalizeRequest,
    principal: Principal = Depends(
        require_roles(Role.channel2_ingest, Role.admin)
    ),
    session: Session = Depends(get_db_session),
) -> MissionEpisodeRead:
    return EpisodicMemoryService(session, principal).finalize_episode(
        episode_id, body
    )


@router.get(
    "/episodes/{episode_id}/events",
    response_model=list[MissionEventRead],
    summary="List episodic events for a mission",
)
def list_events(
    episode_id: UUID,
    principal: Principal = Depends(
        require_roles(
            Role.channel2_ingest,
            Role.channel1_retrieve,
            Role.reviewer,
            Role.admin,
        )
    ),
    session: Session = Depends(get_db_session),
) -> list[MissionEventRead]:
    return EpisodicMemoryService(session, principal).list_events(episode_id)


@router.get(
    "/beliefs",
    response_model=list[BeliefRead],
    summary="List approved consolidated beliefs only",
)
def list_beliefs(
    cognitive_identity_id: UUID = Query(...),
    mission_type: str | None = None,
    principal: Principal = Depends(
        require_roles(
            Role.channel1_retrieve,
            Role.reviewer,
            Role.admin,
        )
    ),
    session: Session = Depends(get_db_session),
) -> list[BeliefRead]:
    return ConsolidatedMemoryService(session, principal).list_approved_beliefs(
        cognitive_identity_id, mission_type=mission_type
    )


@router.get(
    "/beliefs/{belief_id}",
    response_model=BeliefRead,
    summary="Get an approved consolidated belief",
)
def get_belief(
    belief_id: UUID,
    principal: Principal = Depends(
        require_roles(
            Role.channel1_retrieve,
            Role.reviewer,
            Role.admin,
        )
    ),
    session: Session = Depends(get_db_session),
) -> BeliefRead:
    return ConsolidatedMemoryService(session, principal).get_approved_belief(
        belief_id
    )
