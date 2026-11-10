"""Knowledge validation gate - fail-closed before any publication."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from kenwer_c3.db.models import (
    Belief,
    BeliefApprovalStatus,
    BeliefProposal,
    BeliefProposalOperation,
    BeliefVersion,
    MissionEvent,
    PersistentFact,
    SafetyImpact,
)


# Patterns that indicate an attempt to override authoritative flight/regulatory constraints
_AUTHORITATIVE_OVERRIDE_RE = re.compile(
    r"("
    r"flight\s*envelope|geofence|no[- ]?fly|regulatory|far\s*\d|"
    r"authoritative\s*(flight|safety)\s*(policy|constraint)|"
    r"override\s*(altitude|geofence|rth|return[- ]to[- ]home)|"
    r"disable\s*(failsafe|geo[- ]?fence)|"
    r"ignore\s*(regulatory|airspace)"
    r")",
    re.IGNORECASE,
)

_APPLICABILITY_KEYS = (
    "platform_type",
    "platform",
    "mission_type",
    "fleet_id",
    "site_id",
    "environment",
)


@dataclass
class ValidationCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class ValidationReport:
    passed: bool
    checks: list[ValidationCheck] = field(default_factory=list)
    safety_impact: str | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail}
                for c in self.checks
            ],
            "safety_impact": self.safety_impact,
            "errors": self.errors,
        }


class KnowledgeValidationService:
    """Validate a belief proposal against evidence integrity and safety rules."""

    def __init__(self, session: Session, tenant_id: uuid.UUID):
        self.session = session
        self.tenant_id = tenant_id

    def validate_proposal(self, proposal: BeliefProposal) -> ValidationReport:
        checks: list[ValidationCheck] = []
        errors: list[str] = []
        payload = dict(proposal.proposed_payload or {})

        # Non-publication outcomes fail closed for publish paths
        op = proposal.operation
        if op in (
            BeliefProposalOperation.INSUFFICIENT_EVIDENCE,
            BeliefProposalOperation.REDUNDANT,
            BeliefProposalOperation.INCONCLUSIVE,
        ):
            checks.append(
                ValidationCheck(
                    name="publication_eligibility",
                    passed=False,
                    detail=f"Operation {op.value} is not publication-eligible",
                )
            )
            return ValidationReport(
                passed=False,
                checks=checks,
                errors=[f"Operation {op.value} cannot be published"],
            )

        if not payload.get("publication_eligible", True) and op != BeliefProposalOperation.CHALLENGE:
            checks.append(
                ValidationCheck(
                    name="publication_eligibility",
                    passed=False,
                    detail="Proposal marked publication_eligible=false",
                )
            )
            errors.append("Proposal is not publication-eligible")

        # Safety impact must be known
        safety_raw = payload.get("safety_impact", "operational")
        try:
            safety = SafetyImpact(safety_raw)
            checks.append(
                ValidationCheck(
                    name="safety_impact_known",
                    passed=True,
                    detail=f"safety_impact={safety.value}",
                )
            )
        except ValueError:
            checks.append(
                ValidationCheck(
                    name="safety_impact_known",
                    passed=False,
                    detail=f"Unknown safety_impact={safety_raw!r}",
                )
            )
            errors.append("Unknown safety impact - fail closed")
            safety = None

        if safety_raw in (None, "", "unknown"):
            checks.append(
                ValidationCheck(
                    name="safety_impact_not_unknown",
                    passed=False,
                    detail="safety_impact missing or unknown",
                )
            )
            errors.append("Missing/unknown safety impact")

        # Evidence references
        support_ids = [str(x) for x in payload.get("supporting_evidence_ids") or []]
        contra_ids = [str(x) for x in payload.get("contradictory_evidence_ids") or []]
        identity_id = proposal.cognitive_identity_id

        if not support_ids and op != BeliefProposalOperation.CHALLENGE:
            checks.append(
                ValidationCheck(
                    name="supporting_evidence_present",
                    passed=False,
                    detail="No supporting evidence references",
                )
            )
            errors.append("Missing supporting evidence")
        else:
            checks.append(
                ValidationCheck(
                    name="supporting_evidence_present",
                    passed=True,
                    detail=f"{len(support_ids)} supporting refs",
                )
            )

        evidence_ok, evidence_detail = self._verify_evidence_refs(
            support_ids + contra_ids,
            cognitive_identity_id=identity_id,
        )
        checks.append(
            ValidationCheck(
                name="evidence_integrity",
                passed=evidence_ok,
                detail=evidence_detail,
            )
        )
        if not evidence_ok:
            errors.append(evidence_detail)

        # Applicability dimensions
        applicability = dict(payload.get("applicability") or {})
        app_ok, app_detail = self._validate_applicability(applicability)
        checks.append(
            ValidationCheck(name="applicability", passed=app_ok, detail=app_detail)
        )
        if not app_ok:
            errors.append(app_detail)

        # Conflicting / outdated evidence vs existing approved beliefs
        conflict_ok, conflict_detail = self._detect_conflicts(
            proposal, payload, applicability
        )
        checks.append(
            ValidationCheck(
                name="conflict_detection",
                passed=conflict_ok,
                detail=conflict_detail,
            )
        )
        if not conflict_ok and op != BeliefProposalOperation.CHALLENGE:
            # CHALLENGE is allowed to surface conflicts; CREATE/REVISE must not silently override
            if op in (
                BeliefProposalOperation.CREATE,
                BeliefProposalOperation.REVISE,
                BeliefProposalOperation.REFINE,
                BeliefProposalOperation.REINFORCE,
            ):
                # Soft note for reinforce/refine; hard fail for create/revise against hard contradiction
                if "hard_contradiction" in conflict_detail:
                    errors.append(conflict_detail)

        # Authoritative flight / regulatory override attempts
        statement = str(payload.get("statement") or payload.get("claim") or "")
        override_hit = bool(_AUTHORITATIVE_OVERRIDE_RE.search(statement)) or bool(
            _AUTHORITATIVE_OVERRIDE_RE.search(str(applicability))
        )
        checks.append(
            ValidationCheck(
                name="no_authoritative_override",
                passed=not override_hit,
                detail=(
                    "Rejected: proposal attempts to override authoritative "
                    "flight/regulatory constraints"
                    if override_hit
                    else "No authoritative override language detected"
                ),
            )
        )
        if override_hit:
            errors.append("Authoritative flight/regulatory override rejected")

        # Confidence basis labeling present
        basis = payload.get("confidence_basis")
        label = payload.get("evidence_support_label")
        conf_ok = basis in (
            "uncalibrated_model",
            "evidence_support_label",
            "human_set",
        ) and label in ("insufficient", "weak", "moderate", "strong", None)
        if label == "insufficient" and op != BeliefProposalOperation.CHALLENGE:
            conf_ok = False
            errors.append("evidence_support_label=insufficient - fail closed")
        checks.append(
            ValidationCheck(
                name="confidence_labeling",
                passed=conf_ok,
                detail=f"basis={basis} label={label} (uncalibrated scores are not probabilities)",
            )
        )

        passed = all(c.passed for c in checks) and not errors
        # Recompute: if any check failed, fail closed
        if any(not c.passed for c in checks):
            passed = False

        return ValidationReport(
            passed=passed,
            checks=checks,
            safety_impact=safety.value if safety else None,
            errors=errors,
        )

    def _verify_evidence_refs(
        self,
        evidence_ids: list[str],
        *,
        cognitive_identity_id: uuid.UUID | None,
    ) -> tuple[bool, str]:
        if not evidence_ids:
            return True, "No evidence ids to verify"

        missing: list[str] = []
        bad_scope: list[str] = []
        for raw in evidence_ids:
            try:
                eid = uuid.UUID(str(raw))
            except ValueError:
                missing.append(raw)
                continue

            event = self.session.get(MissionEvent, eid)
            if event is not None:
                if event.tenant_id != self.tenant_id:
                    bad_scope.append(raw)
                    continue
                if (
                    cognitive_identity_id is not None
                    and event.cognitive_identity_id != cognitive_identity_id
                ):
                    bad_scope.append(raw)
                    continue
                # Provenance: source_system required on events
                if not event.source_system:
                    missing.append(f"{raw}:missing_source_system")
                continue

            fact = self.session.get(PersistentFact, eid)
            if fact is not None:
                if fact.tenant_id != self.tenant_id:
                    bad_scope.append(raw)
                    continue
                if (
                    cognitive_identity_id is not None
                    and fact.cognitive_identity_id != cognitive_identity_id
                ):
                    bad_scope.append(raw)
                    continue
                continue

            version = self.session.get(BeliefVersion, eid)
            if version is not None:
                if version.tenant_id != self.tenant_id:
                    bad_scope.append(raw)
                    continue
                if (
                    cognitive_identity_id is not None
                    and version.cognitive_identity_id != cognitive_identity_id
                ):
                    bad_scope.append(raw)
                    continue
                if version.approval_status != BeliefApprovalStatus.approved:
                    missing.append(f"{raw}:belief_not_approved")
                continue

            missing.append(raw)

        if missing or bad_scope:
            parts = []
            if missing:
                parts.append(f"missing_or_invalid={missing}")
            if bad_scope:
                parts.append(f"cross_tenant_or_identity={bad_scope}")
            return False, "; ".join(parts)
        return True, f"Verified {len(evidence_ids)} evidence refs in tenant/identity scope"

    def _validate_applicability(
        self, applicability: dict[str, Any]
    ) -> tuple[bool, str]:
        # Fail closed if applicability is empty for operational publication
        declared = {k: applicability.get(k) for k in _APPLICABILITY_KEYS if applicability.get(k)}
        if not declared:
            return False, "Applicability missing required scope dimensions"
        # Conflicting dual platform values
        p1 = applicability.get("platform_type")
        p2 = applicability.get("platform")
        if p1 and p2 and str(p1).lower() != str(p2).lower():
            return False, "Conflicting platform_type vs platform in applicability"
        return True, f"Applicability ok: {sorted(declared)}"

    def _detect_conflicts(
        self,
        proposal: BeliefProposal,
        payload: dict[str, Any],
        applicability: dict[str, Any],
    ) -> tuple[bool, str]:
        if proposal.cognitive_identity_id is None:
            return False, "Missing cognitive_identity_id on proposal"

        beliefs = (
            self.session.execute(
                select(Belief).where(
                    Belief.tenant_id == self.tenant_id,
                    Belief.cognitive_identity_id == proposal.cognitive_identity_id,
                )
            )
            .scalars()
            .all()
        )
        claim = str(payload.get("statement") or payload.get("claim") or "").lower()
        hard = []
        for b in beliefs:
            if proposal.target_belief_id and b.id == proposal.target_belief_id:
                continue
            versions = (
                self.session.execute(
                    select(BeliefVersion).where(
                        BeliefVersion.belief_id == b.id,
                        BeliefVersion.approval_status == BeliefApprovalStatus.approved,
                    )
                )
                .scalars()
                .all()
            )
            if not versions:
                continue
            current = max(versions, key=lambda v: v.version)
            other_app = current.applicability or {}
            # Same mission+platform but negated claim keywords
            same_scope = True
            for k in ("mission_type", "platform_type", "platform"):
                a = applicability.get(k)
                o = other_app.get(k) or (
                    other_app.get("platform") if k == "platform_type" else None
                )
                if a and o and str(a).lower() != str(o).lower():
                    same_scope = False
                    break
            if not same_scope:
                continue
            # Very naive contradiction: "does not" / "not " vs overlapping tokens
            other = current.statement.lower()
            if ("does not" in claim) != ("does not" in other) and len(
                set(claim.split()) & set(other.split())
            ) >= 4:
                hard.append(str(b.id))
        if hard:
            return False, f"hard_contradiction with beliefs={hard}"
        return True, "No hard conflicts detected"
