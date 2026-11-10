"""Deterministic applicability filters for retrieval and reconciliation.

Authorization and applicability always run **before** any semantic ranking.
Similarity scores never authorize access and never decide agreement/contradiction.
"""

from __future__ import annotations

from typing import Any


_SCOPE_KEYS = (
    "platform_type",
    "platform",
    "mission_type",
    "fleet_id",
    "site_id",
    "environment",
)


def _norm(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    return s or None


def applicability_hard_mismatch(
    request_scope: dict[str, Any],
    record_applicability: dict[str, Any] | None,
) -> bool:
    """True when a declared dimension conflicts (hard filter fail).

    Missing dimensions on either side do not fail - only explicit conflicts.
    """
    app = record_applicability or {}
    for key in _SCOPE_KEYS:
        req = _norm(request_scope.get(key))
        # platform / platform_type alias
        if key == "platform_type":
            rec = _norm(app.get("platform_type") or app.get("platform"))
        elif key == "platform":
            rec = _norm(app.get("platform") or app.get("platform_type"))
        else:
            rec = _norm(app.get(key))
        if req is not None and rec is not None and req != rec:
            return True
    return False


def applicability_match_score(
    request_scope: dict[str, Any],
    record_applicability: dict[str, Any] | None,
) -> float:
    """Soft overlap score in [0, 1] after hard mismatch has been ruled out."""
    app = record_applicability or {}
    scored = 0
    matched = 0
    for key in _SCOPE_KEYS:
        req = _norm(request_scope.get(key))
        if key == "platform_type":
            rec = _norm(app.get("platform_type") or app.get("platform"))
        elif key == "platform":
            rec = _norm(app.get("platform") or app.get("platform_type"))
        else:
            rec = _norm(app.get(key))
        if req is None and rec is None:
            continue
        if req is None or rec is None:
            continue
        scored += 1
        if req == rec:
            matched += 1
    if scored == 0:
        return 0.5
    return matched / scored
