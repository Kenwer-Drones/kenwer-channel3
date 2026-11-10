"""Labeled synthetic cases for reasoning-quality evaluation (mock provider).

Expectations are fixture contracts — not claims of calibrated real-world reliability.
"""

from __future__ import annotations

from typing import Any, Literal

CaseId = str
Expectation = dict[str, Any]


CASES: list[dict[str, Any]] = [
    {
        "id": "sufficient_supporting_evidence",
        "description": "Wind + degraded quality → competing hypotheses with grounded citations",
        "include_wind": True,
        "include_quality": True,
        "image_quality_score": 0.62,
        "expect": {
            "c4_abstain": False,
            "min_hypotheses": 1,
            "all_evidence_ids_valid": True,
            "claims_causation_forbidden": True,
            "c5_min_lessons": 1,
            "fabricated_refs": False,
        },
    },
    {
        "id": "multiple_plausible_explanations",
        "description": "Wind and camera hypotheses both present",
        "include_wind": True,
        "include_quality": True,
        "image_quality_score": 0.55,
        "expect": {
            "c4_abstain": False,
            "min_hypotheses": 2,
            "all_evidence_ids_valid": True,
            "claims_causation_forbidden": True,
        },
    },
    {
        "id": "missing_evidence",
        "description": "No wind observation → missing evidence noted / limited lessons",
        "include_wind": False,
        "include_quality": True,
        "image_quality_score": 0.60,
        "expect": {
            "c4_abstain": False,
            "missing_evidence_mentions_wind": True,
            "all_evidence_ids_valid": True,
            "claims_causation_forbidden": True,
        },
    },
    {
        "id": "contradictory_observations",
        "description": "High quality score despite fail expectation path still correlation-only",
        "include_wind": True,
        "include_quality": True,
        "image_quality_score": 0.95,  # contradictory to fail narrative; C3 may pass
        "force_fail_discrepancy": False,
        "expect": {
            # When quality passes, mock may abstain — acceptable
            "allow_abstain_or_hypotheses": True,
            "claims_causation_forbidden": True,
            "all_evidence_ids_valid": True,
        },
    },
    {
        "id": "misleading_correlation",
        "description": "Wind co-occurs with quality drop — must remain correlation_only",
        "include_wind": True,
        "include_quality": True,
        "image_quality_score": 0.50,
        "expect": {
            "c4_abstain": False,
            "all_correlation_only": True,
            "claims_causation_forbidden": True,
            "all_evidence_ids_valid": True,
        },
    },
    {
        "id": "unsupported_causal_claims",
        "description": "Provider must not emit claims_causation=true (mock hard rule)",
        "include_wind": True,
        "include_quality": True,
        "image_quality_score": 0.58,
        "expect": {
            "claims_causation_forbidden": True,
            "all_evidence_ids_valid": True,
        },
    },
    {
        "id": "fabricated_evidence_references",
        "description": "Filter drops invented evidence ids",
        "mode": "unit_filter",
        "expect": {
            "fabricated_refs_rejected": True,
        },
    },
    {
        "id": "incorrect_platform_applicability",
        "description": "C6 CHALLENGE or non-publish when platform mismatches existing belief",
        "mode": "c6_applicability",
        "expect": {
            "challenge_or_create_not_silent_override": True,
        },
    },
    {
        "id": "appropriate_abstention",
        "description": "No actionable discrepancy → abstain, zero lessons",
        "include_wind": False,
        "include_quality": False,
        "image_quality_score": None,
        "expect": {
            "c4_abstain_or_empty": True,
            "c5_zero_lessons_or_abstain": True,
        },
    },
]


ProviderKind = Literal["mock", "llm_external"]
