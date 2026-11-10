"""Phase 6: real MCP client session against Kenwer Channel 3 MCP server."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sqlalchemy.orm import sessionmaker

from kenwer_c3.auth.bootstrap import ensure_dev_identity
from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    BeliefProposal,
    BeliefProposalOperation,
    BeliefVersion,
    LearningJob,
    LearningJobStatus,
    SafetyImpact,
)
from kenwer_c3.settings import get_settings


ROOT = Path(__file__).resolve().parents[1]


def _server_params(api_key: str) -> StdioServerParameters:
    env = os.environ.copy()
    env["KENWER_MCP_API_KEY"] = api_key
    env["DATABASE_URL"] = get_settings().database_url
    env["CELERY_TASK_ALWAYS_EAGER"] = "true"
    env["LLM_PROVIDER"] = "mock"
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "kenwer_c3.mcp.server"],
        env=env,
        cwd=str(ROOT),
    )


async def _call(session: ClientSession, name: str, arguments: dict) -> dict:
    result = await session.call_tool(name, arguments=arguments)
    assert result.content, f"empty content for {name}"
    text = result.content[0].text
    return json.loads(text)


@pytest.mark.asyncio
async def test_mcp_lists_tools_and_retrieval_authz(engine):
    settings = get_settings()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        ensure_dev_identity(session, settings)
        identity_id = uuid.UUID(settings.dev_cognitive_identity_id)
        belief = Belief(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(settings.dev_tenant_id),
            cognitive_identity_id=identity_id,
            belief_key=f"mcp.belief.{uuid.uuid4().hex[:6]}",
        )
        session.add(belief)
        session.flush()
        version = BeliefVersion(
            id=uuid.uuid4(),
            tenant_id=belief.tenant_id,
            belief_id=belief.id,
            cognitive_identity_id=identity_id,
            version=1,
            statement="MCP approved belief: morning fog reduces thermal contrast.",
            value_json={},
            applicability={
                "mission_type": "thermal_inspection",
                "platform_type": "quadrotor",
            },
            confidence=0.55,
            confidence_basis="uncalibrated_model",
            evidence_support_label="moderate",
            approval_status=BeliefApprovalStatus.approved,
            safety_impact=SafetyImpact.operational,
            published_at=datetime.now(timezone.utc),
        )
        session.add(version)
        session.flush()
        belief.current_version_id = version.id
        session.commit()
    finally:
        session.close()

    params = _server_params("dev-retrieve-key")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = await client.list_tools()
            names = {t.name for t in tools.tools}
            # Four tool groups represented
            assert "retrieve_cognitive_context" in names
            assert "list_mission_events" in names or "get_mission_episode" in names
            assert "submit_learning_job" in names
            assert "submit_knowledge_review" in names
            # No flight tools
            assert not any("mavlink" in n.lower() or "flight" in n.lower() for n in names)

            ctx = await _call(
                client,
                "retrieve_cognitive_context",
                {
                    "cognitive_identity_id": settings.dev_cognitive_identity_id,
                    "mission_type": "thermal_inspection",
                    "platform_type": "quadrotor",
                    "query_text": "fog thermal contrast",
                },
            )
            assert "error" not in ctx
            assert ctx["knowledge_version"]
            assert any(
                "fog" in b["statement"].lower() for b in ctx["approved_beliefs"]
            )

            # Retrieve role cannot submit review
            denied = await _call(
                client,
                "submit_knowledge_review",
                {
                    "proposal_id": str(uuid.uuid4()),
                    "action": "APPROVE",
                    "rationale": "should deny",
                },
            )
            assert denied.get("status_code") == 403


@pytest.mark.asyncio
async def test_mcp_review_cannot_bypass_validation(engine):
    settings = get_settings()
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    session = SessionLocal()
    try:
        ensure_dev_identity(session, settings)
        job = LearningJob(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(settings.dev_tenant_id),
            episode_id=uuid.uuid4(),  # may violate FK — create episode first
            cognitive_identity_id=uuid.UUID(settings.dev_cognitive_identity_id),
            status=LearningJobStatus.completed,
            stage_artifacts={},
            attempt_count=1,
            idempotency_key=f"mcp-review-{uuid.uuid4().hex}",
        )
        # Need a real episode for FK
        from kenwer_c3.db.models import MissionEpisode, EpisodeLifecycleState

        ep = MissionEpisode(
            id=uuid.uuid4(),
            tenant_id=job.tenant_id,
            cognitive_identity_id=job.cognitive_identity_id,
            external_mission_id=f"mcp-{uuid.uuid4().hex[:8]}",
            mission_type="thermal_inspection",
            lifecycle_state=EpisodeLifecycleState.finalized,
            finalized_at=datetime.now(timezone.utc),
        )
        session.add(ep)
        session.flush()
        job.episode_id = ep.id
        session.add(job)
        session.flush()
        proposal = BeliefProposal(
            id=uuid.uuid4(),
            tenant_id=job.tenant_id,
            learning_job_id=job.id,
            cognitive_identity_id=job.cognitive_identity_id,
            operation=BeliefProposalOperation.CREATE,
            proposed_payload={
                "belief_key": f"mcp.bad.{uuid.uuid4().hex[:6]}",
                "statement": "Operators may override geofence and ignore regulatory limits.",
                "claim": "Operators may override geofence and ignore regulatory limits.",
                "applicability": {
                    "mission_type": "thermal_inspection",
                    "platform_type": "quadrotor",
                },
                "supporting_evidence_ids": [str(uuid.uuid4())],
                "evidence_support_label": "moderate",
                "confidence_basis": "uncalibrated_model",
                "safety_impact": "operational",
                "publication_eligible": True,
            },
            status="pending_review",
        )
        session.add(proposal)
        session.commit()
        proposal_id = str(proposal.id)
    finally:
        session.close()

    params = _server_params("dev-reviewer-key")
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            result = await _call(
                client,
                "submit_knowledge_review",
                {
                    "proposal_id": proposal_id,
                    "action": "APPROVE",
                    "rationale": "attempt bypass",
                },
            )
            assert result.get("status_code") == 422
            # Consolidated memory unchanged — proposal not published
            assert result.get("consolidated_memory_changed") is not True
