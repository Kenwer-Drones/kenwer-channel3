"""MCP authentication - same API-key RBAC / tenant isolation as REST."""

from __future__ import annotations

import os
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from kenwer_c3.auth.context import Principal, hash_api_key
from kenwer_c3.auth.bootstrap import ensure_dev_identity, parse_dev_api_keys
from kenwer_c3.db.models import ApiKey
from kenwer_c3.domain.common import Role
from kenwer_c3.settings import Settings, get_settings


class McpAuthError(Exception):
    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message)
        self.status_code = status_code


def resolve_principal_from_api_key(
    session: Session,
    raw_key: str | None,
    settings: Settings | None = None,
) -> Principal:
    cfg = settings or get_settings()
    if not raw_key:
        raise McpAuthError("Missing API key (set KENWER_MCP_API_KEY or pass api_key)")

    mapping = parse_dev_api_keys(cfg.dev_api_keys)
    role_name = mapping.get(raw_key)
    if role_name is not None:
        ensure_dev_identity(session, cfg)
        return Principal(
            tenant_id=UUID(cfg.dev_tenant_id),
            role=Role(role_name),
            api_key_id=None,
            cognitive_identity_id=UUID(cfg.dev_cognitive_identity_id),
            key_prefix=raw_key[:12],
        )

    digest = hash_api_key(raw_key)
    row = (
        session.query(ApiKey)
        .filter(
            ApiKey.key_hash == digest,
            ApiKey.key_prefix == raw_key[:12],
            ApiKey.is_active.is_(True),
        )
        .one_or_none()
    )
    if row is None:
        raise McpAuthError("Invalid API key")
    return Principal(
        tenant_id=row.tenant_id,
        role=Role(row.role.value if hasattr(row.role, "value") else str(row.role)),
        api_key_id=row.id,
        cognitive_identity_id=row.cognitive_identity_id,
        key_prefix=row.key_prefix,
    )


def require_mcp_roles(principal: Principal, *roles: Role) -> None:
    if principal.role == Role.admin:
        return
    if principal.role not in roles:
        raise McpAuthError(
            f"Role {principal.role.value} not permitted for this tool",
            status_code=403,
        )


def api_key_from_env(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("KENWER_MCP_API_KEY")


def http_to_mcp(exc: HTTPException) -> McpAuthError:
    return McpAuthError(str(exc.detail), status_code=exc.status_code)
