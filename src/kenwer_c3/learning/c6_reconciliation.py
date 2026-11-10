"""C6 — Belief Reconciliation.

Compares candidate lessons to published consolidated beliefs and emits
structured proposals. Never publishes; never writes BeliefVersion approval
state. Model outputs remain hypothesis-only.

Similarity policy (see docs/reconciliation-similarity.md):
- Deterministic tenant + applicability filters run first.
- Semantic candidate retrieval ranks the filtered set.
- Token Jaccard remains a temporary lexical fallback.
- Similarity alone never determines agreement or contradiction.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    BeliefProposal,
    BeliefProposalOperation,
    CandidateLesson,
    LearningJob,
)
from kenwer_c3.retrieval.applicability import (
    applicability_hard_mismatch,
    applicability_match_score,
)
from kenwer_c3.retrieval.embeddings import cosine_similarity, embed_text


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Outcomes that never require / allow publication
_NON_PUBLICATION_OPS = {
    BeliefProposalOperation.INSUFFICIENT_EVIDENCE,
    BeliefProposalOperation.REDUNDANT,
    BeliefProposalOperation.INCONCLUSIVE,
}


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Temporary lexical fallback — see docs/reconciliation-similarity.md."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _applicability_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Soft applicability overlap for revision ops.

    Hard conflicts → 0.0. One-sided dimensions count as incomplete (< 1.0)
    so REFINE can trigger when a candidate narrows/extends scope without
    contradicting declared values. Similarity still does not decide agreement.
    """
    if applicability_hard_mismatch(a, b):
        return 0.0
    keys = (
        "mission_type",
        "platform_type",
        "platform",
        "fleet_id",
        "site_id",
        "environment",
        "metric",
    )
    scored = 0
    matched = 0
    for k in keys:
        av = a.get(k)
        bv = b.get(k)
        if k == "platform_type":
            bv = b.get("platform_type") or b.get("platform")
            av = a.get("platform_type") or a.get("platform")
        if av is None and bv is None:
            continue
        scored += 1
        if av is not None and bv is not None and str(av).lower() == str(bv).lower():
            matched += 1
    if scored == 0:
        return applicability_match_score(a, b)
    return matched / scored


def _candidate_rank(
    lesson_statement: str,
    version_statement: str,
    *,
    version_embedding: list[float] | None,
) -> tuple[float, float, str]:
    """Return (lexical_jaccard, semantic_cosine, method). Ranking only."""
    claim_tokens = _tokens(lesson_statement)
    lexical = _jaccard(claim_tokens, _tokens(version_statement))
    lesson_vec = embed_text(lesson_statement)
    if version_embedding is not None:
        semantic = cosine_similarity(lesson_vec, version_embedding)
        method = "semantic_embedding+jaccard_fallback"
    else:
        semantic = cosine_similarity(lesson_vec, embed_text(version_statement))
        method = "local_embedding+jaccard_fallback"
    # Combined retrieval score for candidate selection — NOT agreement
    combined = max(lexical, 0.6 * semantic + 0.4 * lexical)
    return combined, lexical, method


def _belief_key_from_claim(claim: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", claim.lower()).strip("_")[:80]
    digest = hashlib.sha256(claim.encode("utf-8")).hexdigest()[:8]
    return f"lesson.{slug or 'claim'}.{digest}"


class BeliefReconciliationService:
    """Produce BeliefProposal rows from candidate lessons vs approved beliefs."""

    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.session = session
        self.tenant_id = tenant_id

    def run(
        self,
        *,
        job_id: uuid.UUID,
        cognitive_identity_id: uuid.UUID,
        evidence_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        job = self.session.get(LearningJob, job_id)
        if job is None or job.tenant_id != self.tenant_id:
            raise ValueError("Learning job not found for tenant")

        # Idempotent re-run: drop prior proposals for this job
        self.session.query(BeliefProposal).filter(
            BeliefProposal.learning_job_id == job_id
        ).delete(synchronize_session=False)

        lessons = (
            self.session.execute(
                select(CandidateLesson).where(
                    CandidateLesson.learning_job_id == job_id,
                    CandidateLesson.tenant_id == self.tenant_id,
                )
            )
            .scalars()
            .all()
        )

        approved = self._load_approved_beliefs(cognitive_identity_id)
        allowed_ids = set((evidence_context or {}).get("allowed_evidence_ids") or [])

        proposals_out: list[dict[str, Any]] = []
        for lesson in lessons:
            proposal = self._reconcile_one(
                job=job,
                lesson=lesson,
                approved=approved,
                allowed_ids=allowed_ids,
            )
            self.session.add(proposal)
            proposals_out.append(self._serialize(proposal))

        if not lessons:
            # Explicit inconclusive abstention for empty C5
            proposal = BeliefProposal(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                learning_job_id=job_id,
                candidate_lesson_id=None,
                target_belief_id=None,
                cognitive_identity_id=cognitive_identity_id,
                operation=BeliefProposalOperation.INCONCLUSIVE,
                proposed_payload={
                    "reason": "No candidate lessons to reconcile",
                    "publication_eligible": False,
                    "confidence_basis": "evidence_support_label",
                    "evidence_support_label": "insufficient",
                },
                contradiction_notes=None,
                status="abstained",
                flags_existing_for_review=False,
                validation_report={"skipped": True, "reason": "no_candidates"},
            )
            self.session.add(proposal)
            proposals_out.append(self._serialize(proposal))

        self.session.flush()
        return {
            "proposals": proposals_out,
            "approved_beliefs_compared": len(approved),
            "consolidated_memory_written": False,
            "publication_performed": False,
        }

    def _load_approved_beliefs(
        self, cognitive_identity_id: uuid.UUID
    ) -> list[tuple[Belief, Any]]:
        beliefs = (
            self.session.execute(
                select(Belief)
                .options(selectinload(Belief.versions))
                .where(
                    Belief.tenant_id == self.tenant_id,
                    Belief.cognitive_identity_id == cognitive_identity_id,
                )
            )
            .scalars()
            .all()
        )
        out: list[tuple[Belief, Any]] = []
        for b in beliefs:
            approved_versions = [
                v
                for v in b.versions
                if v.approval_status == BeliefApprovalStatus.approved
            ]
            if not approved_versions:
                continue
            current = max(approved_versions, key=lambda v: v.version)
            out.append((b, current))
        return out

    def _reconcile_one(
        self,
        *,
        job: LearningJob,
        lesson: CandidateLesson,
        approved: list[tuple[Belief, Any]],
        allowed_ids: set[str],
    ) -> BeliefProposal:
        support_ids = list(
            (lesson.evidence_refs or {}).get("supporting_evidence_ids") or []
        )
        contra_ids = list(
            (lesson.evidence_refs or {}).get("contradictory_evidence_ids") or []
        )
        support_label = (lesson.evidence_refs or {}).get("evidence_support_label")
        uncalibrated = (lesson.evidence_refs or {}).get(
            "model_confidence_uncalibrated"
        )

        applicability = dict(lesson.applicability or {})
        claim_tokens = _tokens(lesson.statement)

        # Evidence gate first
        grounded = [eid for eid in support_ids if not allowed_ids or eid in allowed_ids]
        if not grounded or support_label == "insufficient":
            return BeliefProposal(
                id=uuid.uuid4(),
                tenant_id=self.tenant_id,
                learning_job_id=job.id,
                candidate_lesson_id=lesson.id,
                target_belief_id=None,
                cognitive_identity_id=job.cognitive_identity_id,
                operation=BeliefProposalOperation.INSUFFICIENT_EVIDENCE,
                proposed_payload={
                    "claim": lesson.statement,
                    "applicability": applicability,
                    "supporting_evidence_ids": support_ids,
                    "contradictory_evidence_ids": contra_ids,
                    "evidence_support_label": support_label or "insufficient",
                    "model_confidence_uncalibrated": uncalibrated,
                    "confidence_basis": "uncalibrated_model",
                    "safety_impact": lesson.safety_impact.value
                    if hasattr(lesson.safety_impact, "value")
                    else str(lesson.safety_impact),
                    "publication_eligible": False,
                    "is_hypothesis_only": True,
                },
                contradiction_notes="Insufficient grounded supporting evidence",
                status="abstained",
                flags_existing_for_review=False,
            )

        # 1) Rank candidates (semantic + Jaccard fallback) after tenant load
        # 2) Structured applicability + lexical thresholds decide operations
        # Similarity alone never determines agreement/contradiction.
        best: tuple[Belief, Any, float, float, float, str] | None = None
        for belief, version in approved:
            app = version.applicability or {}
            app_sim = _applicability_overlap(applicability, app)
            emb = list(version.embedding) if version.embedding is not None else None
            combined, lexical, method = _candidate_rank(
                lesson.statement, version.statement, version_embedding=emb
            )
            if best is None or combined > best[2]:
                best = (belief, version, combined, lexical, app_sim, method)

        operation: BeliefProposalOperation
        target_belief_id: uuid.UUID | None = None
        notes: str | None = None
        flags = False
        publication_eligible = True
        status = "pending_review"
        proposed_statement = lesson.statement
        value_json: dict[str, Any] = {
            "source": "candidate_lesson",
            "candidate_lesson_id": str(lesson.id),
        }
        rank_method = "none"

        if best is None or best[2] < 0.25:
            operation = BeliefProposalOperation.CREATE
            belief_key = _belief_key_from_claim(lesson.statement)
        else:
            belief, version, combined, lexical, app_sim, rank_method = best
            target_belief_id = belief.id
            belief_key = belief.belief_key
            existing_support = set(
                str(x)
                for x in (version.provenance or {}).get("supporting_evidence_ids", [])
            )
            new_support = set(grounded)
            # Decision thresholds use structured applicability + lexical fallback —
            # not semantic score alone.
            claim_sim = lexical

            # Hard applicability conflict with lexical claim family overlap → CHALLENGE
            if app_sim == 0.0 and (
                claim_sim >= 0.35 or combined >= 0.45
            ):
                operation = BeliefProposalOperation.CHALLENGE
                notes = (
                    "Candidate claim overlaps an existing belief but applicability "
                    "dimensions conflict; flag for review without replacement. "
                    "Similarity used only for candidate retrieval, not as proof."
                )
                flags = True
                publication_eligible = False
            elif claim_sim >= 0.85 and new_support <= existing_support and app_sim >= 0.8:
                operation = BeliefProposalOperation.REDUNDANT
                notes = "Candidate is redundant with an existing published belief"
                publication_eligible = False
                status = "abstained"
            elif claim_sim >= 0.7 and new_support - existing_support and app_sim >= 0.6:
                operation = BeliefProposalOperation.REINFORCE
                notes = "Additional supporting evidence for an existing belief"
                value_json["reinforce_evidence_ids"] = sorted(new_support - existing_support)
            elif claim_sim >= 0.55 and app_sim >= 0.5 and app_sim < 1.0:
                operation = BeliefProposalOperation.REFINE
                notes = "Same claim family with refined applicability scope"
            elif claim_sim >= 0.4:
                if claim_sim < 0.95:
                    operation = BeliefProposalOperation.REVISE
                    notes = "Proposed revision of existing belief statement/value"
                else:
                    operation = BeliefProposalOperation.INCONCLUSIVE
                    publication_eligible = False
                    status = "abstained"
                    notes = "Claim similarity ambiguous; abstaining"
            else:
                operation = BeliefProposalOperation.CREATE
                belief_key = _belief_key_from_claim(lesson.statement)
                target_belief_id = None

        payload = {
            "belief_key": belief_key,
            "claim": proposed_statement,
            "statement": proposed_statement,
            "value_json": value_json,
            "applicability": applicability,
            "supporting_evidence_ids": grounded,
            "contradictory_evidence_ids": contra_ids,
            "evidence_support_label": support_label or "moderate",
            "model_confidence_uncalibrated": uncalibrated,
            "confidence_basis": "uncalibrated_model",
            "safety_impact": lesson.safety_impact.value
            if hasattr(lesson.safety_impact, "value")
            else str(lesson.safety_impact),
            "limitations": applicability.get("limitations"),
            "uncertainty": applicability.get("uncertainty"),
            "publication_eligible": publication_eligible
            and operation not in _NON_PUBLICATION_OPS,
            "is_hypothesis_only": True,
            "match": None
            if target_belief_id is None or best is None
            else {
                "belief_id": str(target_belief_id),
                "retrieval_score": round(best[2], 3),
                "lexical_jaccard": round(best[3], 3),
                "applicability_overlap": round(best[4], 3),
                "ranking_method": best[5],
                "note": (
                    "retrieval_score ranks candidates only; operation decisions "
                    "require applicability + structured rules"
                ),
            },
        }

        return BeliefProposal(
            id=uuid.uuid4(),
            tenant_id=self.tenant_id,
            learning_job_id=job.id,
            candidate_lesson_id=lesson.id,
            target_belief_id=target_belief_id,
            cognitive_identity_id=job.cognitive_identity_id,
            operation=operation,
            proposed_payload=payload,
            contradiction_notes=notes,
            status=status,
            flags_existing_for_review=flags,
        )

    @staticmethod
    def _serialize(p: BeliefProposal) -> dict[str, Any]:
        return {
            "id": str(p.id),
            "operation": p.operation.value
            if hasattr(p.operation, "value")
            else str(p.operation),
            "status": p.status,
            "target_belief_id": str(p.target_belief_id) if p.target_belief_id else None,
            "candidate_lesson_id": str(p.candidate_lesson_id)
            if p.candidate_lesson_id
            else None,
            "flags_existing_for_review": p.flags_existing_for_review,
            "proposed_payload": p.proposed_payload,
            "contradiction_notes": p.contradiction_notes,
        }
