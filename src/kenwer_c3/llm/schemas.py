"""Strict Pydantic schemas for model outputs (hypotheses only).

LLM numeric confidence scores are **uncalibrated model scores**, not
probabilities. Prefer discrete ``evidence_support_label`` values for decisions.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

EvidenceSupportLabelLiteral = Literal[
    "insufficient", "weak", "moderate", "strong"
]


def label_from_uncalibrated_score(score: float) -> EvidenceSupportLabelLiteral:
    """Map an uncalibrated model score to a discrete evidence-support label."""
    if score < 0.35:
        return "insufficient"
    if score < 0.55:
        return "weak"
    if score < 0.75:
        return "moderate"
    return "strong"


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(description="Must match an id from the provided evidence context")
    note: str | None = None


class AttributionHypothesisOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    model_confidence_uncalibrated: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Uncalibrated model-assigned score. NOT a calibrated probability. "
            "Do not treat as P(hypothesis|evidence)."
        ),
    )
    evidence_support_label: EvidenceSupportLabelLiteral | None = Field(
        default=None,
        description="Discrete evidence-support label (preferred over numeric score).",
    )
    supporting_evidence: list[EvidenceCitation] = Field(default_factory=list)
    contradictory_evidence: list[EvidenceCitation] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    uncertainty: str
    claims_causation: bool = False
    correlation_only: bool = True

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_confidence_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and "confidence" in data and (
            "model_confidence_uncalibrated" not in data
        ):
            data = dict(data)
            data["model_confidence_uncalibrated"] = data.pop("confidence")
        return data

    @model_validator(mode="after")
    def _default_support_label(self) -> AttributionHypothesisOut:
        if self.evidence_support_label is None:
            object.__setattr__(
                self,
                "evidence_support_label",
                label_from_uncalibrated_score(self.model_confidence_uncalibrated),
            )
        return self

    @property
    def confidence(self) -> float:
        """Backward-compatible alias for the uncalibrated model score."""
        return self.model_confidence_uncalibrated


class AttributionAnalysisOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    discrepancy_metric: str
    hypotheses: list[AttributionHypothesisOut] = Field(default_factory=list)
    abstain: bool = False
    abstain_reason: str | None = None
    is_hypothesis_only: bool = True


class CandidateLessonOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    applicability: dict[str, Any] = Field(default_factory=dict)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradictory_evidence_ids: list[str] = Field(default_factory=list)
    limitations: str
    uncertainty: str
    safety_impact: str = "operational"
    is_hypothesis_only: bool = True
    model_confidence_uncalibrated: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional uncalibrated model score — not a calibrated probability.",
    )
    evidence_support_label: EvidenceSupportLabelLiteral | None = None

    @field_validator("evidence_support_label", mode="before")
    @classmethod
    def _normalize_label(cls, v: Any) -> Any:
        if v is None:
            return v
        return str(v).lower()


class CandidateLessonsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lessons: list[CandidateLessonOut] = Field(default_factory=list)
    abstain: bool = False
    abstain_reason: str | None = None
    is_hypothesis_only: bool = True


PROMPT_VERSION_ATTRIBUTION = "c4_attribution_v2"
PROMPT_VERSION_LESSONS = "c5_lessons_v2"
