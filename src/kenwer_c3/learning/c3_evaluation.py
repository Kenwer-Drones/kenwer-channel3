"""C3 - Deterministic Outcome Evaluation (no causation / no belief generation)."""

from __future__ import annotations

import uuid
from typing import Any

from kenwer_c3.domain.learning import (
    DiscrepancyReportRead,
    EpisodeReconstruction,
    EvaluationResultStatus,
    ExpectationBaseline,
    OutcomeEvaluationResult,
)
from kenwer_c3.learning.metrics import (
    METRIC_REGISTRY,
    compare_numeric,
    get_metric,
    resolve_path,
)


class OutcomeEvaluationService:
    def evaluate(
        self,
        reconstruction: EpisodeReconstruction,
        baseline: ExpectationBaseline,
        *,
        requested_metrics: list[str] | None = None,
    ) -> OutcomeEvaluationResult:
        criteria = {**baseline.metric_baselines, **baseline.criteria_json}
        metric_names = requested_metrics or self._infer_metrics(criteria)

        reports: list[DiscrepancyReportRead] = []
        for name in metric_names:
            reports.append(
                self._evaluate_metric(name, criteria, reconstruction, baseline)
            )

        summary = {
            "pass": sum(1 for r in reports if r.result_status == EvaluationResultStatus.pass_),
            "fail": sum(1 for r in reports if r.result_status == EvaluationResultStatus.fail),
            "unknown": sum(
                1 for r in reports if r.result_status == EvaluationResultStatus.unknown
            ),
            "not_applicable": sum(
                1
                for r in reports
                if r.result_status == EvaluationResultStatus.not_applicable
            ),
            "causation_inferred": False,
            "beliefs_generated": False,
        }
        return OutcomeEvaluationResult(
            episode_id=reconstruction.episode_id,
            expectation_snapshot_id=baseline.snapshot_id,
            reports=reports,
            summary=summary,
        )

    def _infer_metrics(self, criteria: dict[str, Any]) -> list[str]:
        names: list[str] = []
        for metric in METRIC_REGISTRY.values():
            if resolve_path(criteria, metric.expected_path) is not None:
                names.append(metric.name)
            elif metric.name in criteria:
                names.append(metric.name)
        # Also honor explicit metrics list in criteria
        explicit = criteria.get("metrics")
        if isinstance(explicit, list):
            for item in explicit:
                if isinstance(item, str) and item not in names:
                    names.append(item)
        return names

    def _evaluate_metric(
        self,
        name: str,
        criteria: dict[str, Any],
        reconstruction: EpisodeReconstruction,
        baseline: ExpectationBaseline,
    ) -> DiscrepancyReportRead:
        metric = get_metric(name)
        if metric is None:
            return DiscrepancyReportRead(
                episode_id=reconstruction.episode_id,
                metric_name=name,
                expected_value={"raw": criteria.get(name)},
                observed_value={},
                discrepancy={"reason": "unsupported_metric"},
                result_status=EvaluationResultStatus.unknown,
                unit=None,
                comparison_operator=None,
                evidence_refs={},
                measurement_quality={"supported": False},
                uncertainty={"note": "Metric is not in the deterministic registry"},
                missing_evidence={"metric_definition": True},
            )

        expected_raw = resolve_path(criteria, metric.expected_path)
        if expected_raw is None and name in criteria:
            expected_raw = criteria[name]
        if isinstance(expected_raw, dict) and "value" in expected_raw:
            expected_unit = expected_raw.get("unit", metric.unit)
            expected_num = expected_raw.get("value")
        else:
            expected_unit = metric.unit
            expected_num = expected_raw

        if expected_num is None:
            return DiscrepancyReportRead(
                episode_id=reconstruction.episode_id,
                metric_name=name,
                expected_value={},
                observed_value={},
                discrepancy={"reason": "missing_expected_baseline"},
                result_status=EvaluationResultStatus.unknown,
                unit=metric.unit,
                comparison_operator=metric.comparison.value,
                evidence_refs={},
                measurement_quality={},
                uncertainty={"note": "Expected baseline value missing"},
                missing_evidence={"expected_value": True},
            )

        # Unit validation when provided in criteria
        if isinstance(expected_raw, dict) and expected_raw.get("unit"):
            if expected_raw["unit"] != metric.unit:
                return DiscrepancyReportRead(
                    episode_id=reconstruction.episode_id,
                    metric_name=name,
                    expected_value={"value": expected_num, "unit": expected_raw["unit"]},
                    observed_value={},
                    discrepancy={"reason": "invalid_unit", "expected_unit": metric.unit},
                    result_status=EvaluationResultStatus.unknown,
                    unit=metric.unit,
                    comparison_operator=metric.comparison.value,
                    evidence_refs={},
                    measurement_quality={"unit_mismatch": True},
                    uncertainty={"note": "Unit does not match metric definition"},
                    missing_evidence={},
                )

        matching_events = [
            e
            for e in reconstruction.events
            if e.event_type == metric.observation_event_type and not e.is_duplicate_key
        ]
        evidence_artifact_ids = [str(a.id) for a in reconstruction.evidence]

        if not matching_events:
            # Distinguish missing observation vs negative
            if metric.observation_event_type in reconstruction.negative_observations:
                return DiscrepancyReportRead(
                    episode_id=reconstruction.episode_id,
                    metric_name=name,
                    expected_value={"value": expected_num, "unit": expected_unit},
                    observed_value={"status": "negative"},
                    discrepancy={"reason": "negative_observation"},
                    result_status=EvaluationResultStatus.fail
                    if metric.required
                    else EvaluationResultStatus.not_applicable,
                    unit=metric.unit,
                    comparison_operator=metric.comparison.value,
                    evidence_refs={"artifact_ids": evidence_artifact_ids},
                    measurement_quality={"observation": "negative"},
                    uncertainty={},
                    missing_evidence={},
                )
            status = (
                EvaluationResultStatus.unknown
                if metric.required
                else EvaluationResultStatus.not_applicable
            )
            return DiscrepancyReportRead(
                episode_id=reconstruction.episode_id,
                metric_name=name,
                expected_value={"value": expected_num, "unit": expected_unit},
                observed_value={},
                discrepancy={"reason": "missing_observation"},
                result_status=status,
                unit=metric.unit,
                comparison_operator=metric.comparison.value,
                evidence_refs={"artifact_ids": evidence_artifact_ids},
                measurement_quality={},
                uncertainty={"note": "No observation event present; value not invented"},
                missing_evidence={"observation_event": metric.observation_event_type},
            )

        # Use chronologically last matching observation (deterministic)
        event = matching_events[-1]
        observed_raw = resolve_path(event.payload, metric.observed_path)
        if observed_raw is None and "value" in event.payload:
            observed_raw = event.payload["value"]

        observed_unit = event.payload.get("unit", metric.unit)
        if observed_unit != metric.unit and event.payload.get("unit"):
            return DiscrepancyReportRead(
                episode_id=reconstruction.episode_id,
                metric_name=name,
                expected_value={"value": expected_num, "unit": expected_unit},
                observed_value={"value": observed_raw, "unit": observed_unit},
                discrepancy={"reason": "invalid_unit"},
                result_status=EvaluationResultStatus.unknown,
                unit=metric.unit,
                comparison_operator=metric.comparison.value,
                evidence_refs={
                    "event_id": str(event.id),
                    "artifact_ids": evidence_artifact_ids,
                },
                measurement_quality=dict(event.source_quality or {}),
                uncertainty={"note": "Observed unit mismatch"},
                missing_evidence={},
            )

        try:
            observed_num = float(observed_raw)
            expected_f = float(expected_num)
        except (TypeError, ValueError):
            return DiscrepancyReportRead(
                episode_id=reconstruction.episode_id,
                metric_name=name,
                expected_value={"value": expected_num, "unit": expected_unit},
                observed_value={"value": observed_raw, "unit": observed_unit},
                discrepancy={"reason": "non_numeric_value"},
                result_status=EvaluationResultStatus.unknown,
                unit=metric.unit,
                comparison_operator=metric.comparison.value,
                evidence_refs={"event_id": str(event.id)},
                measurement_quality=dict(event.source_quality or {}),
                uncertainty={"note": "Could not coerce values to float"},
                missing_evidence={},
            )

        passed = compare_numeric(
            observed_num,
            expected_f,
            metric.comparison,
            tolerance=metric.tolerance or 0.0,
        )
        delta = observed_num - expected_f
        return DiscrepancyReportRead(
            episode_id=reconstruction.episode_id,
            metric_name=name,
            expected_value={"value": expected_f, "unit": expected_unit},
            observed_value={"value": observed_num, "unit": observed_unit},
            discrepancy={
                "delta": delta,
                "passed": passed,
                "comparison": metric.comparison.value,
            },
            result_status=EvaluationResultStatus.pass_
            if passed
            else EvaluationResultStatus.fail,
            unit=metric.unit,
            comparison_operator=metric.comparison.value,
            evidence_refs={
                "event_id": str(event.id),
                "event_idempotency_key": event.event_idempotency_key,
                "artifact_ids": evidence_artifact_ids,
                "expectation_snapshot_id": str(baseline.snapshot_id),
            },
            measurement_quality=dict(event.source_quality or {}),
            uncertainty={},
            missing_evidence={},
        )
