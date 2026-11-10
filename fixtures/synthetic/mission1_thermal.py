"""Synthetic Mission 1 - thermal inspection with below-threshold image quality.

All sensor values are explicitly labeled synthetic. Do not treat as real flight data.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID


MISSION_TYPE = "thermal_inspection"
SYNTHETIC_LABEL = "synthetic_fixture_mission_1_thermal"


def expectation_payload(*, captured_at: datetime | None = None) -> dict[str, Any]:
    return {
        "criteria_json": {
            "image_quality_min": 0.85,
            "wind_speed_max_mps": 8.0,
            "trajectory_rmse_max_m": 1.5,
        },
        "metric_baselines": {
            "image_quality": 0.90,
        },
        "mission_plan_version": "plan-thermal-v1",
        "source_system": "channel1",
        "source_event_id": "approval-synthetic-001",
        "captured_at": (captured_at or datetime.now(timezone.utc)).isoformat(),
    }


def episode_payload(cognitive_identity_id: UUID | str, external_mission_id: str) -> dict[str, Any]:
    return {
        "cognitive_identity_id": str(cognitive_identity_id),
        "external_mission_id": external_mission_id,
        "mission_type": MISSION_TYPE,
        "objectives": {
            "inspect": "rooftop_hvac",
            "synthetic": True,
            "fixture": SYNTHETIC_LABEL,
        },
        "approved_plan": {
            "version": "plan-thermal-v1",
            "waypoints": 4,
            "synthetic": True,
        },
        "site_context": {"site_id": "plant-7-synthetic", "synthetic": True},
        "platform_context": {"platform": "quadrotor", "payload": "thermal", "synthetic": True},
    }


def observation_events(
    *,
    episode_id: UUID | str,
    cognitive_identity_id: UUID | str,
    base_time: datetime | None = None,
) -> list[dict[str, Any]]:
    """Synthetic observations: image quality below expectation, wind and trajectory present."""
    t0 = base_time or datetime.now(timezone.utc)
    eid = str(episode_id)
    cid = str(cognitive_identity_id)
    return [
        {
            "episode_id": eid,
            "cognitive_identity_id": cid,
            "event_idempotency_key": "synth-m1-wind-001",
            "event_type": "observation.wind",
            "sequence_no": 1,
            "payload": {"wind_mps": 7.2, "unit": "m_s", "synthetic": True},
            "source_system": "channel2",
            "source_quality": {"sensor": "anemometer_sim", "quality": "nominal"},
            "recorded_at": (t0 + timedelta(seconds=10)).isoformat(),
            "provenance": {"fixture": SYNTHETIC_LABEL},
        },
        {
            "episode_id": eid,
            "cognitive_identity_id": cid,
            "event_idempotency_key": "synth-m1-quality-001",
            "event_type": "observation.image_quality",
            "sequence_no": 2,
            "payload": {"score": 0.62, "unit": "score", "synthetic": True},
            "source_system": "channel2",
            "source_quality": {"sensor": "thermal_cam_sim", "quality": "degraded"},
            "recorded_at": (t0 + timedelta(seconds=20)).isoformat(),
            "provenance": {"fixture": SYNTHETIC_LABEL},
        },
        {
            "episode_id": eid,
            "cognitive_identity_id": cid,
            "event_idempotency_key": "synth-m1-traj-001",
            "event_type": "observation.trajectory",
            "sequence_no": 3,
            "payload": {"rmse_m": 0.4, "unit": "m", "synthetic": True},
            "source_system": "channel2",
            "source_quality": {"sensor": "gnss_sim", "quality": "nominal"},
            "recorded_at": (t0 + timedelta(seconds=30)).isoformat(),
            "provenance": {"fixture": SYNTHETIC_LABEL},
        },
    ]


def finalize_payload() -> dict[str, Any]:
    return {
        "actual_outcomes": {
            "image_quality": 0.62,
            "wind_mps": 7.2,
            "trajectory_rmse_m": 0.4,
            "synthetic": True,
        },
        "missing_data": {},
    }
