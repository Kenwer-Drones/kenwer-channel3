"""Observability adapters (Agent Beacon optional, disabled by default)."""

from kenwer_c3.observability.beacon import NullBeaconAdapter, get_beacon_adapter

__all__ = ["NullBeaconAdapter", "get_beacon_adapter"]
