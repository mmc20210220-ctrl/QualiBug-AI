"""Public permission-matrix authority with hierarchy and precedence reduction."""
from __future__ import annotations

from typing import Any

from .delegated_permission_precedence import apply_effective_permission_precedence
from .fact_permission_matrix_core import (
    materialize_fact_permission_matrix as _materialize_fact_permission_matrix,
)
from .role_actor_coordinate import disambiguate_role_actor_coordinates
from .segregation_of_duties_authority import apply_sod_permission_policies
from .role_inheritance_authority import (
    apply_role_inheritance_permissions,
    materialize_role_inheritance_contracts,
)


def materialize_fact_permission_matrix(asset: dict[str, Any]) -> dict[str, Any]:
    disambiguate_role_actor_coordinates(asset)
    materialize_role_inheritance_contracts(asset)
    materialized = _materialize_fact_permission_matrix(asset)
    inherited = apply_role_inheritance_permissions(materialized)
    effective = apply_effective_permission_precedence(inherited)
    return apply_sod_permission_policies(effective)


__all__ = ["materialize_fact_permission_matrix"]
