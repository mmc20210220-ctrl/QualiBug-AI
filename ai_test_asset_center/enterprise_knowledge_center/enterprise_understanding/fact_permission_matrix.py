"""Public permission-matrix authority with effective delegated-permission reduction."""
from __future__ import annotations

from typing import Any

from .delegated_permission_precedence import apply_delegated_permission_precedence
from .fact_permission_matrix_core import (
    materialize_fact_permission_matrix as _materialize_fact_permission_matrix,
)


def materialize_fact_permission_matrix(asset: dict[str, Any]) -> dict[str, Any]:
    return apply_delegated_permission_precedence(
        _materialize_fact_permission_matrix(asset)
    )


__all__ = ["materialize_fact_permission_matrix"]
