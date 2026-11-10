"""Channel 1 cognitive retrieval HTTP API."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from kenwer_c3.auth.context import Principal, get_db_session, require_roles
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.retrieval import CognitiveContextResponse, CognitiveRetrievalRequest
from kenwer_c3.retrieval.engine import CognitiveRetrievalEngine

router = APIRouter(prefix="/v1", tags=["retrieval"])


@router.post(
    "/retrieve/cognitive-context",
    response_model=CognitiveContextResponse,
    summary="Assemble Channel 1 cognitive context from approved memory",
)
def retrieve_cognitive_context(
    body: CognitiveRetrievalRequest,
    principal: Principal = Depends(
        require_roles(Role.channel1_retrieve, Role.admin, Role.reviewer)
    ),
    session: Session = Depends(get_db_session),
) -> CognitiveContextResponse:
    return CognitiveRetrievalEngine(session, principal).retrieve(body)
