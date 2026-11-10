"""Authentication and authorization boundary."""

from kenwer_c3.auth.context import Principal, require_principal, require_roles
from kenwer_c3.auth.bootstrap import ensure_dev_identity

__all__ = [
    "Principal",
    "require_principal",
    "require_roles",
    "ensure_dev_identity",
]
