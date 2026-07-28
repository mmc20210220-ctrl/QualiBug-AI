"""Install source-bound joins and formal non-HTTP observers on planning.

``discovery_runtime_planning.build_discovery_plan`` resolves its Behavior IR builder and
obligation compiler from module globals at execution time. This compatibility entry replaces
those symbols with stable additive wrappers:

* exact accepted rule/interface identities;
* canonical-field response observers;
* exact source-declared UI contracts;
* formal UI obligations using the one registered experiment-mainline authority.
"""
from __future__ import annotations

from typing import Any

from . import discovery_runtime_planning as _planning
from .effect_observer_binding import bind_source_effect_observers
from .formal_ui_surface import install_formal_ui_surface
from .formal_ui_surface_guard import install_formal_ui_read_only_guard
from .non_http_observers import install_non_http_observers
from .semantic_operation_binding import bind_accepted_semantic_operations
from .source_ui_contract_binding import bind_source_ui_contracts
from .source_ui_obligation_binding import install_source_ui_obligation_binding

_INSTALL_MARKER = "_qualibug_semantic_operation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_behavior_ir_builder"

# Register formal observation and UI protocol surfaces before any obligation or experiment is
# compiled. Every installer is idempotent and performs no target I/O.
install_non_http_observers()
install_formal_ui_surface()
install_formal_ui_read_only_guard()
install_source_ui_obligation_binding()


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
    """Build canonical IR and apply only exact source-grounded joins."""

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
    ui_ir, _ui_receipt = bind_source_ui_contracts(
        observer_ir,
        asset if isinstance(asset, dict) else {},
    )
    return ui_ir


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
