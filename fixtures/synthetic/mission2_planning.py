"""Synthetic Mission 2 - planning-time retrieval after Mission 1 publication.

All values labeled synthetic. Not real flight data.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


MISSION_TYPE = "thermal_inspection"
SYNTHETIC_LABEL = "synthetic_fixture_mission_2_planning"


def planning_retrieval_request(
    cognitive_identity_id: UUID | str,
    *,
    query_text: str = "wind thermal image quality coastal inspection",
) -> dict[str, Any]:
    return {
        "cognitive_identity_id": str(cognitive_identity_id),
        "mission_objective": "Plan follow-up rooftop HVAC thermal inspection",
        "mission_type": MISSION_TYPE,
        "platform_type": "quadrotor",
        "payload_configuration": {"payload": "thermal", "synthetic": True},
        "site_id": "plant-7-synthetic",
        "fleet_id": "fleet-a",
        "environment": "coastal",
        "operating_conditions": {"forecast_wind_mps": 6.5, "synthetic": True},
        "requested_memory_types": ["persistent", "consolidated"],
        "query_text": query_text,
        "max_beliefs": 20,
        "max_facts": 50,
        "include_episodic_limit": 0,
    }


def wrong_platform_retrieval_request(
    cognitive_identity_id: UUID | str,
) -> dict[str, Any]:
    req = planning_retrieval_request(cognitive_identity_id)
    req["platform_type"] = "fixed_wing"
    req["query_text"] = "Elevated wind may correlate with reduced thermal image quality"
    return req
