"""Asymptote Labs Agent Beacon - optional observability adapter boundary.

Disabled by default. Never stores authoritative mission knowledge.
Never transmits mission content externally unless explicitly configured.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from kenwer_c3.settings import Settings, get_settings

logger = logging.getLogger(__name__)


class BeaconAdapter(Protocol):
    """Future observability integration surface."""

    @property
    def enabled(self) -> bool: ...

    def emit_event(self, name: str, attributes: dict[str, Any] | None = None) -> None: ...

    def emit_span(self, name: str, attributes: dict[str, Any] | None = None) -> None: ...


class NullBeaconAdapter:
    """No-op adapter used when Beacon is disabled (default)."""

    @property
    def enabled(self) -> bool:
        return False

    def emit_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        return None

    def emit_span(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        return None


class LoggingBeaconAdapter:
    """Local debug adapter - redacts mission payloads; never authoritative memory."""

    def __init__(self, settings: Settings):
        self._settings = settings

    @property
    def enabled(self) -> bool:
        return True

    def emit_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        safe = _redact(attributes or {})
        logger.info("beacon.event name=%s attrs=%s", name, safe)

    def emit_span(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        safe = _redact(attributes or {})
        logger.debug("beacon.span name=%s attrs=%s", name, safe)


def _redact(attrs: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in attrs.items():
        lowered = k.lower()
        if any(
            s in lowered
            for s in (
                "payload",
                "mission",
                "evidence",
                "secret",
                "token",
                "password",
                "api_key",
            )
        ):
            out[k] = "[redacted_mission_or_secret]"
        else:
            out[k] = v
    return out


def get_beacon_adapter(settings: Settings | None = None) -> BeaconAdapter:
    cfg = settings or get_settings()
    if not cfg.beacon_enabled:
        return NullBeaconAdapter()
    # Explicit opt-in only. External OTLP transmission requires endpoint config.
    if cfg.beacon_otlp_endpoint:
        logger.warning(
            "Beacon OTLP endpoint configured (%s) but export client is deferred; "
            "using logging adapter. Mission content is still redacted.",
            cfg.beacon_otlp_endpoint,
        )
    return LoggingBeaconAdapter(cfg)
