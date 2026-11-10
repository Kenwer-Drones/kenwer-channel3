"""Request principal resolution from Bearer API keys.

Never trust tenant_id / cognitive_identity_id from the request body alone —
they must match the authenticated principal's verified scope.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from kenwer_c3.auth.bootstrap import ensure_dev_identity, parse_dev_api_keys
from kenwer_c3.db.base import get_session_factory
from kenwer_c3.db.models import ApiKey, ApiKeyRole
from kenwer_c3.domain.common import Role
from kenwer_c3.settings import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    tenant_id: UUID
    role: Role
    api_key_id: UUID | None
    cognitive_identity_id: UUID | None
    key_prefix: str

    def assert_tenant(self, tenant_id: UUID) -> None:
        if tenant_id != self.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-tenant access denied",
            )

    def assert_identity_access(self, cognitive_identity_id: UUID) -> None:
        """Keys bound to an identity may only act on that identity."""
        if (
            self.cognitive_identity_id is not None
            and self.cognitive_identity_id != cognitive_identity_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cognitive identity out of scope for this credential",
            )


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def get_db_session():
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _resolve_dev_principal(raw_key: str, settings: Settings) -> Principal | None:
    mapping = parse_dev_api_keys(settings.dev_api_keys)
    role_name = mapping.get(raw_key)
    if role_name is None:
        return None
    try:
        role = Role(role_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Invalid configured role for API key: {role_name}",
        ) from exc
    return Principal(
        tenant_id=UUID(settings.dev_tenant_id),
        role=role,
        api_key_id=None,
        cognitive_identity_id=UUID(settings.dev_cognitive_identity_id),
        key_prefix=raw_key[:12],
    )


def _resolve_db_principal(session: Session, raw_key: str) -> Principal | None:
    digest = hash_api_key(raw_key)
    prefix = raw_key[:12]
    row = (
        session.query(ApiKey)
        .filter(
            ApiKey.key_hash == digest,
            ApiKey.key_prefix == prefix,
            ApiKey.is_active.is_(True),
        )
        .one_or_none()
    )
    if row is None:
        return None
    return Principal(
        tenant_id=row.tenant_id,
        role=Role(row.role.value),
        api_key_id=row.id,
        cognitive_identity_id=row.cognitive_identity_id,
        key_prefix=row.key_prefix,
    )


def require_principal(
    authorization: str | None = Header(default=None),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer API key",
            headers={"WWW-Authenticate": "Bearer"},
        )
    raw_key = authorization.split(" ", 1)[1].strip()
    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty API key",
        )

    # Ensure local seed identity exists for development credentials
    if settings.app_env == "development":
        ensure_dev_identity(session, settings)

    principal = _resolve_db_principal(session, raw_key)
    if principal is None:
        principal = _resolve_dev_principal(raw_key, settings)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    return principal


def require_roles(*roles: Role):
    allowed = set(roles)

    def _checker(principal: Principal = Depends(require_principal)) -> Principal:
        if principal.role == Role.admin:
            return principal
        if principal.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{principal.role.value}' not permitted for this operation",
            )
        return principal

    return _checker


# Re-export for typing convenience
__all__ = [
    "Principal",
    "require_principal",
    "require_roles",
    "get_db_session",
    "hash_api_key",
    "ApiKeyRole",
]
