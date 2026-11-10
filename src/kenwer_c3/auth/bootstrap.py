"""Development identity bootstrap (local credentials only)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from kenwer_c3.db.models import CognitiveIdentity, Tenant
from kenwer_c3.settings import Settings


def parse_dev_api_keys(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        key, role = part.split(":", 1)
        mapping[key.strip()] = role.strip()
    return mapping


def ensure_dev_identity(session: Session, settings: Settings) -> tuple[Tenant, CognitiveIdentity]:
    tenant_id = UUID(settings.dev_tenant_id)
    identity_id = UUID(settings.dev_cognitive_identity_id)

    tenant = session.get(Tenant, tenant_id)
    if tenant is None:
        tenant = Tenant(id=tenant_id, name="dev-tenant", is_active=True)
        session.add(tenant)
        session.flush()

    identity = session.get(CognitiveIdentity, identity_id)
    if identity is None:
        identity = CognitiveIdentity(
            id=identity_id,
            tenant_id=tenant_id,
            slug="dev-drone-1",
            display_name="Development Thermal Drone",
            fleet_id="dev-fleet",
            drone_id="dev-drone-1",
            platform_type="quadrotor",
        )
        session.add(identity)
        session.flush()

    return tenant, identity
