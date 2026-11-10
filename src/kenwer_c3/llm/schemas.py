"""Strict Pydantic schemas for model outputs (hypotheses only)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EvidenceCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(description="Must match an id from the provided evidence context")
    note: str | None = None


class AttributionHypothesisOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: list[EvidenceCitation] = Field(default_factory=list)
    contradictory_evidence: list[EvidenceCitation] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    uncertainty: str
    claims_causation: bool = False
    correlation_only: bool = True


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
    limitations: str
    uncertainty: str
    safety_impact: str = "operational"
    is_hypothesis_only: bool = True


class CandidateLessonsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lessons: list[CandidateLessonOut] = Field(default_factory=list)
    abstain: bool = False
    abstain_reason: str | None = None
    is_hypothesis_only: bool = True


PROMPT_VERSION_ATTRIBUTION = "c4_attribution_v1"
PROMPT_VERSION_LESSONS = "c5_lessons_v1"
