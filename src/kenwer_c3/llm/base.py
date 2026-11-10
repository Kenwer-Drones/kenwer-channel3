"""Provider-independent reasoning interface."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from kenwer_c3.llm.schemas import AttributionAnalysisOut, CandidateLessonsOut


class ReasoningProviderError(Exception):
    """Raised on provider timeout, transport failure, or invalid structured output."""


@runtime_checkable
class ReasoningProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    def analyze_attribution(
        self,
        *,
        evidence_context: dict[str, Any],
        discrepancy: dict[str, Any],
    ) -> tuple[AttributionAnalysisOut, dict[str, Any]]:
        """Return (validated analysis, execution_metadata)."""
        ...

    def generate_lessons(
        self,
        *,
        evidence_context: dict[str, Any],
        attribution: AttributionAnalysisOut,
        discrepancy: dict[str, Any],
    ) -> tuple[CandidateLessonsOut, dict[str, Any]]:
        """Return (validated lessons, execution_metadata)."""
        ...
