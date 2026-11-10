"""Audit log helper — never logs secrets or raw sensitive mission payloads by default."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from kenwer_c3.db.models import AuditLog, MemoryFamily


def write_audit(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    actor_key_id: uuid.UUID | None = None,
    memory_family: MemoryFamily | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    # Strip common sensitive keys if callers accidentally include them
    safe_details = dict(details or {})
    for key in list(safe_details):
        lowered = key.lower()
        if any(s in lowered for s in ("secret", "password", "api_key", "token", "authorization")):
            safe_details[key] = "[redacted]"

    entry = AuditLog(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        actor_key_id=actor_key_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        memory_family=memory_family,
        details=safe_details,
    )
    session.add(entry)
    return entry
