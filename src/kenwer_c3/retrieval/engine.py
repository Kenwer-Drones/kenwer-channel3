"""Cognitive Retrieval Engine — Channel 1 context assembly.

Sequence:
1. Authenticate via Principal (caller)
2. Resolve permitted identity / fleet / site scopes
3. Persistent facts (tenant + identity)
4. Approved active consolidated belief versions only
5. Optional episodic historical context
6. Deterministic applicability filters BEFORE semantic ranking
7. Structured context with evidence, limitations, confidence basis, knowledge version
8. Explicit no-applicable-knowledge when empty

Never returns proposed / rejected / superseded / rolled_back as active knowledge.
Never generates flight commands.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from kenwer_c3.auth.context import Principal
from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    CognitiveIdentity,
    FactStatus,
    MissionEpisode,
    PersistentFact,
)
from kenwer_c3.domain.retrieval import (
    CognitiveContextResponse,
    CognitiveRetrievalRequest,
    EvidenceRef,
    MemoryTypeRequest,
    RetrievedBelief,
    RetrievedEpisodeSummary,
    RetrievedFact,
)
from kenwer_c3.memory.audit import write_audit
from kenwer_c3.retrieval.applicability import (
    applicability_hard_mismatch,
    applicability_match_score,
)
from kenwer_c3.retrieval.embeddings import cosine_similarity, embed_text

KNOWLEDGE_VERSION = "c3-retrieval-v1"


class CognitiveRetrievalEngine:
    def __init__(self, session: Session, principal: Principal):
        self.session = session
        self.principal = principal

    def retrieve(self, request: CognitiveRetrievalRequest) -> CognitiveContextResponse:
        identity = self._resolve_identity(request.cognitive_identity_id)
        scope = self._build_scope(request, identity)
        filters_applied = [
            "tenant_isolation",
            "cognitive_identity_scope",
            "approved_active_beliefs_only",
            "applicability_hard_filter_before_ranking",
        ]
        limitations = [
            "Retrieval does not issue flight commands or bypass Channel 1 validation.",
            "Semantic ranking is advisory only; it never authorizes access or "
            "determines agreement/contradiction.",
            "Token Jaccard / local embeddings are uncalibrated lexical helpers.",
        ]
        notes: list[str] = []

        facts: list[RetrievedFact] = []
        beliefs: list[RetrievedBelief] = []
        episodes: list[RetrievedEpisodeSummary] = []

        requested = set(request.requested_memory_types)

        if MemoryTypeRequest.persistent in requested:
            facts = self._retrieve_facts(identity, scope, request.max_facts)
            filters_applied.append("persistent_active_facts")

        if MemoryTypeRequest.consolidated in requested:
            beliefs = self._retrieve_beliefs(
                identity, scope, request, notes=notes
            )
            filters_applied.append("consolidated_approved_current_only")

        if MemoryTypeRequest.episodic in requested and request.include_episodic_limit > 0:
            episodes = self._retrieve_episodes(
                identity, scope, request, notes=notes
            )
            filters_applied.append("episodic_historical_context_optional")

        no_knowledge = not facts and not beliefs and not episodes
        if no_knowledge:
            notes.append("no_applicable_knowledge")

        try:
            write_audit(
                self.session,
                tenant_id=self.principal.tenant_id,
                actor_key_id=self.principal.api_key_id,
                action="cognitive_retrieve",
                resource_type="cognitive_context",
                resource_id=str(identity.id),
                details={
                    "mission_type": request.mission_type,
                    "fact_count": len(facts),
                    "belief_count": len(beliefs),
                    "episode_count": len(episodes),
                    "no_applicable_knowledge": no_knowledge,
                    "actor_key_prefix": self.principal.key_prefix,
                },
            )
        except Exception:  # noqa: BLE001
            # Audit best-effort — retrieval must still succeed
            notes.append("audit_write_skipped")

        return CognitiveContextResponse(
            tenant_id=self.principal.tenant_id,
            cognitive_identity_id=identity.id,
            knowledge_version=KNOWLEDGE_VERSION,
            no_applicable_knowledge=no_knowledge,
            scope=scope,
            persistent_facts=facts,
            approved_beliefs=beliefs,
            episodic_context=episodes,
            limitations=limitations,
            filters_applied=filters_applied,
            notes=notes,
        )

    def _resolve_identity(self, cognitive_identity_id: uuid.UUID) -> CognitiveIdentity:
        self.principal.assert_identity_access(cognitive_identity_id)
        identity = self.session.get(CognitiveIdentity, cognitive_identity_id)
        if identity is None or identity.tenant_id != self.principal.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Cognitive identity not found in tenant scope",
            )
        return identity

    def _build_scope(
        self, request: CognitiveRetrievalRequest, identity: CognitiveIdentity
    ) -> dict[str, Any]:
        return {
            "tenant_id": str(self.principal.tenant_id),
            "cognitive_identity_id": str(identity.id),
            "fleet_id": request.fleet_id or identity.fleet_id,
            "drone_id": identity.drone_id,
            "site_id": request.site_id,
            "platform_type": request.platform_type or identity.platform_type,
            "mission_type": request.mission_type,
            "environment": request.environment,
            "payload_configuration": request.payload_configuration,
            "operating_conditions": request.operating_conditions,
            "mission_objective": request.mission_objective,
        }

    def _retrieve_facts(
        self,
        identity: CognitiveIdentity,
        scope: dict[str, Any],
        limit: int,
    ) -> list[RetrievedFact]:
        rows = (
            self.session.execute(
                select(PersistentFact).where(
                    PersistentFact.tenant_id == self.principal.tenant_id,
                    PersistentFact.cognitive_identity_id == identity.id,
                    PersistentFact.status == FactStatus.active,
                )
            )
            .scalars()
            .all()
        )
        out: list[RetrievedFact] = []
        for fact in rows:
            # Facts may carry applicability in value_json or provenance
            app = {}
            if isinstance(fact.value_json, dict):
                app = dict(fact.value_json.get("applicability") or {})
            if applicability_hard_mismatch(scope, app):
                continue
            score = applicability_match_score(scope, app) if app else None
            out.append(
                RetrievedFact(
                    id=fact.id,
                    fact_key=fact.fact_key,
                    category=fact.category,
                    version=fact.version,
                    value_json=dict(fact.value_json or {}),
                    provenance=dict(fact.provenance or {}),
                    applicability_score=score,
                )
            )
        return out[:limit]

    def _retrieve_beliefs(
        self,
        identity: CognitiveIdentity,
        scope: dict[str, Any],
        request: CognitiveRetrievalRequest,
        *,
        notes: list[str],
    ) -> list[RetrievedBelief]:
        beliefs = (
            self.session.execute(
                select(Belief)
                .options(selectinload(Belief.versions))
                .where(
                    Belief.tenant_id == self.principal.tenant_id,
                    Belief.cognitive_identity_id == identity.id,
                )
            )
            .scalars()
            .all()
        )

        query = " ".join(
            x
            for x in [
                request.query_text,
                request.mission_objective,
                request.mission_type,
                scope.get("environment"),
            ]
            if x
        )
        query_vec = embed_text(query) if query else None

        candidates: list[tuple[RetrievedBelief, float]] = []
        for belief in beliefs:
            version = self._current_approved_only(belief)
            if version is None:
                continue
            if applicability_hard_mismatch(scope, version.applicability):
                continue

            app_score = applicability_match_score(scope, version.applicability)
            semantic = 0.0
            method = "applicability_only"
            if query_vec is not None:
                # Prefer stored embedding when present; else deterministic local embed
                if version.embedding is not None:
                    semantic = cosine_similarity(query_vec, list(version.embedding))
                    method = "pgvector_embedding+applicability"
                else:
                    semantic = cosine_similarity(
                        query_vec, embed_text(version.statement)
                    )
                    method = "local_embedding+applicability"
                # Similarity never overrides a failed applicability filter (already applied)
                # and never alone decides inclusion — require non-hard-mismatch already done.

            # Combined ranking: applicability dominates; semantic is tie-break / boost
            rank = (0.7 * app_score) + (0.3 * max(semantic, 0.0))

            evidence_ids = (version.provenance or {}).get("supporting_evidence_ids") or []
            evidence_refs = [
                EvidenceRef(evidence_id=eid, kind="supporting", note=None)
                for eid in evidence_ids
            ]
            candidates.append(
                (
                    RetrievedBelief(
                        belief_id=belief.id,
                        belief_key=belief.belief_key,
                        belief_version_id=version.id,
                        version=version.version,
                        statement=version.statement,
                        value_json=dict(version.value_json or {}),
                        applicability=dict(version.applicability or {}),
                        confidence=version.confidence,
                        confidence_basis=version.confidence_basis
                        or "uncalibrated_model",
                        evidence_support_label=version.evidence_support_label,
                        safety_impact=version.safety_impact,
                        limitations=version.limitations,
                        published_at=version.published_at,
                        evidence_refs=evidence_refs,
                        ranking_score=round(rank, 4),
                        ranking_method=method,
                    ),
                    rank,
                )
            )

        candidates.sort(key=lambda x: x[1], reverse=True)
        if query_vec is not None:
            notes.append(
                "semantic_candidate_ranking_applied_after_deterministic_filters"
            )
        return [c[0] for c in candidates[: request.max_beliefs]]

    def _retrieve_episodes(
        self,
        identity: CognitiveIdentity,
        scope: dict[str, Any],
        request: CognitiveRetrievalRequest,
        *,
        notes: list[str],
    ) -> list[RetrievedEpisodeSummary]:
        rows = (
            self.session.execute(
                select(MissionEpisode)
                .where(
                    MissionEpisode.tenant_id == self.principal.tenant_id,
                    MissionEpisode.cognitive_identity_id == identity.id,
                )
                .order_by(MissionEpisode.created_at.desc())
                .limit(max(request.include_episodic_limit * 3, 10))
            )
            .scalars()
            .all()
        )
        query = request.query_text or request.mission_objective or ""
        qvec = embed_text(query) if query else None
        out: list[tuple[RetrievedEpisodeSummary, float]] = []
        for ep in rows:
            app = {
                "mission_type": ep.mission_type,
                "platform_type": (ep.platform_context or {}).get("platform_type")
                or (ep.platform_context or {}).get("platform"),
                "site_id": (ep.site_context or {}).get("site_id")
                if isinstance(ep.site_context, dict)
                else None,
                "fleet_id": identity.fleet_id,
            }
            if applicability_hard_mismatch(scope, app):
                continue
            app_score = applicability_match_score(scope, app)
            semantic = 0.0
            if qvec is not None:
                text = f"{ep.mission_type or ''} {ep.external_mission_id or ''}"
                semantic = cosine_similarity(qvec, embed_text(text))
            rank = 0.7 * app_score + 0.3 * max(semantic, 0.0)
            finalized = None
            if hasattr(ep, "finalized_at"):
                finalized = ep.finalized_at
            out.append(
                (
                    RetrievedEpisodeSummary(
                        episode_id=ep.id,
                        external_mission_id=ep.external_mission_id,
                        mission_type=ep.mission_type,
                        lifecycle_state=ep.lifecycle_state.value
                        if hasattr(ep.lifecycle_state, "value")
                        else str(ep.lifecycle_state),
                        finalized_at=finalized,
                        ranking_score=round(rank, 4),
                    ),
                    rank,
                )
            )
        out.sort(key=lambda x: x[1], reverse=True)
        notes.append("episodic_records_are_historical_context_not_validated_beliefs")
        return [x[0] for x in out[: request.include_episodic_limit]]

    @staticmethod
    def _current_approved_only(belief: Belief):
        approved = [
            v
            for v in belief.versions
            if v.approval_status == BeliefApprovalStatus.approved
        ]
        if not approved:
            return None
        if belief.current_version_id:
            for v in approved:
                if v.id == belief.current_version_id:
                    # Double-check not superseded pointer drift
                    if v.approval_status == BeliefApprovalStatus.approved:
                        return v
        return max(approved, key=lambda v: v.version)
