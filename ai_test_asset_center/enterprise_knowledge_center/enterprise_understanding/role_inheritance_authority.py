"""Stable public surface for source-governed role inheritance."""
from __future__ import annotations

from .role_inheritance_contract_authority import (
    CONTRACT_SCHEMA,
    RECEIPT_SCHEMA,
    materialize_role_inheritance_contracts,
)
from .role_inheritance_permission_authority import apply_role_inheritance_permissions


__all__ = [
    "CONTRACT_SCHEMA",
    "RECEIPT_SCHEMA",
    "apply_role_inheritance_permissions",
    "materialize_role_inheritance_contracts",
]
