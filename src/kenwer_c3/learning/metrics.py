"""Typed metric registry for deterministic outcome evaluation.

No causation inference. Unknown metrics / unit mismatches yield unknown or fail-closed
results - never invented sensor values.
"""

from __future__ import annotations

from kenwer_c3.domain.learning import MetricComparison, MetricDefinition

METRIC_REGISTRY: dict[str, MetricDefinition] = {
    "image_quality": MetricDefinition(
        name="image_quality",
        unit="score",
        comparison=MetricComparison.gte,
        expected_path="image_quality_min",
        observation_event_type="observation.image_quality",
        observed_path="score",
        required=True,
        tolerance=0.0,
    ),
    "wind_speed": MetricDefinition(
        name="wind_speed",
        unit="m_s",
        comparison=MetricComparison.lte,
        expected_path="wind_speed_max_mps",
        observation_event_type="observation.wind",
        observed_path="wind_mps",
        required=False,
        tolerance=0.0,
    ),
    "trajectory_rmse": MetricDefinition(
        name="trajectory_rmse",
        unit="m",
        comparison=MetricComparison.lte,
        expected_path="trajectory_rmse_max_m",
        observation_event_type="observation.trajectory",
        observed_path="rmse_m",
        required=False,
        tolerance=0.0,
    ),
}


def get_metric(name: str) -> MetricDefinition | None:
    return METRIC_REGISTRY.get(name)


def resolve_path(data: dict, path: str):
    """Resolve dotted path; returns None if missing - never invents values."""
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def compare_numeric(
    observed: float,
    expected: float,
    op: MetricComparison,
    *,
    tolerance: float = 0.0,
) -> bool:
    if op == MetricComparison.gte:
        return observed + tolerance >= expected
    if op == MetricComparison.lte:
        return observed - tolerance <= expected
    if op == MetricComparison.gt:
        return observed > expected - tolerance
    if op == MetricComparison.lt:
        return observed < expected + tolerance
    if op == MetricComparison.eq:
        return abs(observed - expected) <= tolerance
    raise ValueError(f"Unsupported comparison operator: {op}")
