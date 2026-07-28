"""Install source-bound joins and formal non-HTTP observers on planning.

``discovery_runtime_planning.build_discovery_plan`` resolves its Behavior IR
builder from module globals at execution time. The product compatibility entry
imports this module once, replacing that builder with a stable wrapper. The
wrapper delegates all IR construction to the original authority, then adds only
exact accepted rule/interface identities and canonical-field response observers.
"""
from __future__ import annotations

from typing import Any

from . import discovery_runtime_planning as _planning
from .effect_observer_binding import bind_source_effect_observers
from .non_http_observers import install_non_http_observers
from .semantic_operation_binding import bind_accepted_semantic_operations

_INSTALL_MARKER = "_qualibug_semantic_operation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_behavior_ir_builder"

# Register formal process-ledger observation before any experiment is compiled.
install_non_http_observers()


if hasattr(_planning, _ORIGINAL_MARKER):
    _original_build_behavior_ir = getattr(_planning, _ORIGINAL_MARKER)
else:
    _original_build_behavior_ir = _planning.build_behavior_ir_from_knowledge_asset
    setattr(_planning, _ORIGINAL_MARKER, _original_build_behavior_ir)


def build_behavior_ir_with_semantic_operation_bindings(
    asset: dict[str, Any] | None,
    *,
    project_id: str = "",
    source_snapshot_hash: str = "",
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    available_surfaces: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Build canonical IR, exact semantic joins, then exact effect observers."""

    behavior_ir = _original_build_behavior_ir(
        asset,
        project_id=project_id,
        source_snapshot_hash=source_snapshot_hash,
        api_operations=api_operations,
        runtime_actors=runtime_actors,
        available_surfaces=available_surfaces,
    )
    semantic_ir, _semantic_receipt = bind_accepted_semantic_operations(
        behavior_ir,
        asset if isinstance(asset, dict) else {},
    )
    observer_ir, _observer_receipt = bind_source_effect_observers(semantic_ir)
    return observer_ir


if not getattr(_planning, _INSTALL_MARKER, False):
    _planning.build_behavior_ir_from_knowledge_asset = (
        build_behavior_ir_with_semantic_operation_bindings
    )
    setattr(_planning, _INSTALL_MARKER, True)


build_discovery_plan = _planning.build_discovery_plan

__all__ = [
    "build_behavior_ir_with_semantic_operation_bindings",
    "build_discovery_plan",
]
