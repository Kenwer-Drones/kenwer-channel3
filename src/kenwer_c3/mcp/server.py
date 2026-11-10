"""Kenwer Channel 3 MCP server - four tool groups, no flight control.

Uses the official Python MCP SDK (FastMCP). Reuses existing backend services.
Read-only by default; knowledge review requires reviewer/admin and cannot
bypass validation or directly mutate published consolidated memory outside
BeliefPublicationService.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer

from kenwer_c3.db.base import get_session_factory
from kenwer_c3.db.models import ValidationAction
from kenwer_c3.domain.common import Role
from kenwer_c3.domain.learning import LearningJobCreate
from kenwer_c3.domain.retrieval import CognitiveRetrievalRequest, MemoryTypeRequest
from kenwer_c3.domain.review import ReviewAction
from kenwer_c3.learning.jobs import LearningJobService
from kenwer_c3.learning.pipeline import LearningPipeline
from kenwer_c3.mcp.auth import (
    McpAuthError,
    api_key_from_env,
    http_to_mcp,
    require_mcp_roles,
    resolve_principal_from_api_key,
)
from kenwer_c3.memory.consolidated.publication import BeliefPublicationService
from kenwer_c3.memory.consolidated.service import ConsolidatedMemoryService
from kenwer_c3.memory.episodic.service import EpisodicMemoryService
from kenwer_c3.memory.persistent.service import PersistentMemoryService
from kenwer_c3.retrieval.engine import CognitiveRetrievalEngine
from kenwer_c3.settings import get_settings

mcp = MCPServer(
    "kenwer-c3",
    instructions=(
        "Kenwer Channel 3 cognitive memory tools. "
        "Read-only by default. No MAVLink or flight-control tools. "
        "Review tools cannot bypass knowledge validation or silently "
        "rewrite published consolidated memory."
    ),
)


def _json(data: Any) -> str:
    return json.dumps(data, default=str)


def _session():
    return get_session_factory()()


def _auth(session, api_key: str | None):
    return resolve_principal_from_api_key(session, api_key_from_env(api_key))


# ---------------------------------------------------------------------------
# Group 1 - Memory retrieval (read-only)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="retrieve_cognitive_context",
    description=(
        "Assemble Channel 1 cognitive context from persistent facts and "
        "approved consolidated beliefs. Never returns proposed/rejected/"
        "superseded/rolled_back beliefs as active knowledge."
    ),
)
def retrieve_cognitive_context(
    cognitive_identity_id: str,
    mission_type: str | None = None,
    platform_type: str | None = None,
    fleet_id: str | None = None,
    site_id: str | None = None,
    environment: str | None = None,
    mission_objective: str | None = None,
    query_text: str | None = None,
    include_episodic: bool = False,
    api_key: str | None = None,
) -> str:
    session = _session()
    try:
        principal = _auth(session, api_key)
        require_mcp_roles(
            principal, Role.channel1_retrieve, Role.reviewer, Role.admin
        )
        types = [
            MemoryTypeRequest.persistent,
            MemoryTypeRequest.consolidated,
        ]
        if include_episodic:
            types.append(MemoryTypeRequest.episodic)
        req = CognitiveRetrievalRequest(
            cognitive_identity_id=uuid.UUID(cognitive_identity_id),
            mission_type=mission_type,
            platform_type=platform_type,
            fleet_id=fleet_id,
            site_id=site_id,
            environment=environment,
            mission_objective=mission_objective,
            query_text=query_text,
            requested_memory_types=types,
            include_episodic_limit=5 if include_episodic else 0,
        )
        result = CognitiveRetrievalEngine(session, principal).retrieve(req)
        session.commit()
        return _json(result.model_dump(mode="json"))
    except McpAuthError as exc:
        session.rollback()
        return _json({"error": str(exc), "status_code": exc.status_code})
    except HTTPException as exc:
        session.rollback()
        err = http_to_mcp(exc)
        return _json({"error": str(err), "status_code": err.status_code})
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return _json({"error": f"{type(exc).__name__}: {exc}", "status_code": 500})
    finally:
        session.close()


@mcp.tool(
    name="list_approved_beliefs",
    description="List approved consolidated beliefs for a cognitive identity (read-only).",
)
def list_approved_beliefs(
    cognitive_identity_id: str,
    mission_type: str | None = None,
    api_key: str | None = None,
) -> str:
    session = _session()
    try:
        principal = _auth(session, api_key)
        require_mcp_roles(
            principal, Role.channel1_retrieve, Role.reviewer, Role.admin
        )
        rows = ConsolidatedMemoryService(session, principal).list_approved_beliefs(
            uuid.UUID(cognitive_identity_id), mission_type=mission_type
        )
        session.commit()
        return _json([r.model_dump(mode="json") for r in rows])
    except McpAuthError as exc:
        session.rollback()
        return _json({"error": str(exc), "status_code": exc.status_code})
    except HTTPException as exc:
        session.rollback()
        err = http_to_mcp(exc)
        return _json({"error": str(err), "status_code": err.status_code})
    finally:
        session.close()


@mcp.tool(
    name="list_persistent_facts",
    description="List active persistent facts for a cognitive identity (read-only).",
)
def list_persistent_facts(
    cognitive_identity_id: str,
    fact_key: str | None = None,
    api_key: str | None = None,
) -> str:
    session = _session()
    try:
        principal = _auth(session, api_key)
        require_mcp_roles(
            principal,
            Role.channel1_retrieve,
            Role.channel2_ingest,
            Role.reviewer,
            Role.admin,
        )
        rows = PersistentMemoryService(session, principal).list_facts(
            uuid.UUID(cognitive_identity_id),
            fact_key=fact_key,
            active_only=True,
        )
        session.commit()
        return _json([r.model_dump(mode="json") for r in rows])
    except McpAuthError as exc:
        session.rollback()
        return _json({"error": str(exc), "status_code": exc.status_code})
    except HTTPException as exc:
        session.rollback()
        err = http_to_mcp(exc)
        return _json({"error": str(err), "status_code": err.status_code})
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Group 2 - Mission evidence retrieval (read-only)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="get_mission_episode",
    description="Get a mission episode within the caller's tenant/identity scope.",
)
def get_mission_episode(episode_id: str, api_key: str | None = None) -> str:
    session = _session()
    try:
        principal = _auth(session, api_key)
        require_mcp_roles(
            principal,
            Role.channel1_retrieve,
            Role.channel2_ingest,
            Role.reviewer,
            Role.admin,
        )
        row = EpisodicMemoryService(session, principal).get_episode(
            uuid.UUID(episode_id)
        )
        session.commit()
        return _json(row.model_dump(mode="json"))
    except McpAuthError as exc:
        session.rollback()
        return _json({"error": str(exc), "status_code": exc.status_code})
    except HTTPException as exc:
        session.rollback()
        err = http_to_mcp(exc)
        return _json({"error": str(err), "status_code": err.status_code})
    finally:
        session.close()


@mcp.tool(
    name="list_mission_events",
    description="List episodic mission events for an episode (historical evidence).",
)
def list_mission_events(episode_id: str, api_key: str | None = None) -> str:
    session = _session()
    try:
        principal = _auth(session, api_key)
        require_mcp_roles(
            principal,
            Role.channel1_retrieve,
            Role.channel2_ingest,
            Role.reviewer,
            Role.admin,
        )
        rows = EpisodicMemoryService(session, principal).list_events(
            uuid.UUID(episode_id)
        )
        session.commit()
        return _json([r.model_dump(mode="json") for r in rows])
    except McpAuthError as exc:
        session.rollback()
        return _json({"error": str(exc), "status_code": exc.status_code})
    except HTTPException as exc:
        session.rollback()
        err = http_to_mcp(exc)
        return _json({"error": str(err), "status_code": err.status_code})
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Group 3 - Offline evaluation job submission / status
# ---------------------------------------------------------------------------


@mcp.tool(
    name="submit_learning_job",
    description=(
        "Enqueue a C1–C6 offline evaluation/learning job for a finalized episode. "
        "Does not publish consolidated memory."
    ),
)
def submit_learning_job(episode_id: str, api_key: str | None = None) -> str:
    session = _session()
    try:
        principal = _auth(session, api_key)
        require_mcp_roles(principal, Role.channel2_ingest, Role.admin)
        settings = get_settings()
        job = LearningJobService(session, principal).create(
            LearningJobCreate(episode_id=uuid.UUID(episode_id))
        )
        if settings.celery_task_always_eager:
            LearningPipeline(session).run(job.id)
        else:
            from kenwer_c3.workers.tasks import run_learning_job

            async_result = run_learning_job.delay(str(job.id))
            row = LearningJobService(session, principal).get(job.id)
            # attach task id via fresh get after delay
            from kenwer_c3.db.models import LearningJob

            db_job = session.get(LearningJob, job.id)
            if db_job is not None:
                db_job.celery_task_id = async_result.id
        session.commit()
        return _json(
            LearningJobService(session, principal).get(job.id).model_dump(mode="json")
        )
    except McpAuthError as exc:
        session.rollback()
        return _json({"error": str(exc), "status_code": exc.status_code})
    except HTTPException as exc:
        session.rollback()
        err = http_to_mcp(exc)
        return _json({"error": str(err), "status_code": err.status_code})
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return _json({"error": f"{type(exc).__name__}: {exc}", "status_code": 500})
    finally:
        session.close()


@mcp.tool(
    name="get_learning_job_status",
    description="Get learning job status and stage artifacts (C1–C6).",
)
def get_learning_job_status(job_id: str, api_key: str | None = None) -> str:
    session = _session()
    try:
        principal = _auth(session, api_key)
        require_mcp_roles(
            principal,
            Role.admin,
            Role.channel2_ingest,
            Role.channel1_retrieve,
            Role.reviewer,
        )
        row = LearningJobService(session, principal).get(uuid.UUID(job_id))
        session.commit()
        return _json(row.model_dump(mode="json"))
    except McpAuthError as exc:
        session.rollback()
        return _json({"error": str(exc), "status_code": exc.status_code})
    except HTTPException as exc:
        session.rollback()
        err = http_to_mcp(exc)
        return _json({"error": str(err), "status_code": err.status_code})
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Group 4 - Knowledge review submission (reviewer/admin only)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="submit_knowledge_review",
    description=(
        "Submit APPROVE/REJECT/DEFER for a belief proposal. "
        "Cannot bypass knowledge validation. Cannot directly edit published "
        "belief content outside the publication service. CHALLENGE approvals "
        "flag only - they do not replace published beliefs."
    ),
)
def submit_knowledge_review(
    proposal_id: str,
    action: str,
    rationale: str | None = None,
    api_key: str | None = None,
) -> str:
    session = _session()
    try:
        principal = _auth(session, api_key)
        require_mcp_roles(principal, Role.reviewer, Role.admin)
        action_norm = action.strip().upper()
        try:
            review_action = ReviewAction(action_norm)
        except ValueError:
            return _json(
                {
                    "error": "action must be APPROVE, REJECT, or DEFER",
                    "status_code": 400,
                }
            )
        action_map = {
            ReviewAction.approve: ValidationAction.approve,
            ReviewAction.reject: ValidationAction.reject,
            ReviewAction.defer: ValidationAction.defer,
        }
        result = BeliefPublicationService(session, principal).review_proposal(
            uuid.UUID(proposal_id),
            action=action_map[review_action],
            rationale=rationale,
        )
        session.commit()
        return _json(result)
    except McpAuthError as exc:
        session.rollback()
        return _json({"error": str(exc), "status_code": exc.status_code})
    except HTTPException as exc:
        session.rollback()
        return _json(
            {
                "error": exc.detail,
                "status_code": exc.status_code,
            }
        )
    except Exception as exc:  # noqa: BLE001
        session.rollback()
        return _json({"error": f"{type(exc).__name__}: {exc}", "status_code": 500})
    finally:
        session.close()


def main() -> None:
    # stdio transport for MCP hosts / integration tests
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
