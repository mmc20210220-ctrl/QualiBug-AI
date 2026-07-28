"""Install source-bound joins and formal non-HTTP observers on planning.

``discovery_runtime_planning.build_discovery_plan`` resolves its Behavior IR builder and
obligation compiler from module globals at execution time. This compatibility entry replaces
those symbols with stable additive wrappers:

* exact accepted rule/interface identities;
* canonical-field response observers;
* enterprise and explicit scan UI contracts;
* formal UI obligations using the one registered experiment-mainline authority.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from . import discovery_runtime_planning as _planning
from .effect_observer_binding import bind_source_effect_observers
from .formal_ui_surface import install_formal_ui_surface
from .formal_ui_surface_guard import install_formal_ui_read_only_guard
from .non_http_observers import install_non_http_observers
from .scan_ui_contract_overlay import (
    bind_scan_ui_contract_context,
    overlay_scan_ui_contracts,
    reset_scan_ui_contract_context,
)
from .semantic_operation_binding import bind_accepted_semantic_operations
from .source_ui_contract_binding import bind_source_ui_contracts
from .source_ui_contract_source_guard import (
    install_source_ui_contract_source_guard,
)
from .source_ui_obligation_binding import install_source_ui_obligation_binding

_INSTALL_MARKER = "_qualibug_semantic_operation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_behavior_ir_builder"

# Register formal observation and UI protocol surfaces before any obligation or experiment is
# compiled. Every installer is idempotent and performs no target I/O.
install_non_http_observers()
install_formal_ui_surface()
install_formal_ui_read_only_guard()
install_source_ui_contract_source_guard()
install_source_ui_obligation_binding()


if hasattr(_planning, _ORIGINAL_MARKER):
    _original_build_behavior_ir = getattr(_planning, _ORIGINAL_MARKER)
else:
    _original_build_behavior_ir = _planning.build_behavior_ir_from_knowledge_asset
    setattr(_planning, _ORIGINAL_MARKER, _original_build_behavior_ir)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _planning_inputs_with_declared_adapters(inputs: Any) -> Any:
    """Give planning one adapter declaration regardless of which public entry called it.

    ``pipeline_runtime`` already copies top-level ``declared_adapters`` into the runtime
    contract. Direct planning callers did not, so the IR could say ui_browser=true while the
    experiment compiler still saw http-only (or vice versa). Merge once here and pass a copied
    frozen dataclass; never mutate the caller's campaign context.
    """
    context = dict(_dict(getattr(inputs, "campaign_context", {})))
    submitted = [
        _text(value)
        for value in _list(context.get("declared_adapters"))
        if _text(value)
    ]
    runtime = dict(_dict(context.get("_runtime_contract")))
    runtime_declared = [
        _text(value)
        for value in _list(runtime.get("declared_adapters"))
        if _text(value)
    ]
    merged = list(dict.fromkeys([*runtime_declared, *submitted]))
    if merged or "declared_adapters" in context or "declared_adapters" in runtime:
        runtime["declared_adapters"] = merged
        context["_runtime_contract"] = runtime
    if context == _dict(getattr(inputs, "campaign_context", {})):
        return inputs
    return replace(inputs, campaign_context=context)


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

    effective_asset, scan_ui_receipt = overlay_scan_ui_contracts(asset)
    behavior_ir = _original_build_behavior_ir(
        effective_asset,
        project_id=project_id,
        source_snapshot_hash=source_snapshot_hash,
        api_operations=api_operations,
        runtime_actors=runtime_actors,
        available_surfaces=available_surfaces,
    )
    semantic_ir, _semantic_receipt = bind_accepted_semantic_operations(
        behavior_ir,
        effective_asset,
    )
    observer_ir, _observer_receipt = bind_source_effect_observers(semantic_ir)
    ui_ir, _ui_receipt = bind_source_ui_contracts(
        observer_ir,
        effective_asset,
    )
    ui_ir["scan_ui_contract_overlay_receipt"] = dict(scan_ui_receipt)
    return ui_ir


if not getattr(_planning, _INSTALL_MARKER, False):
    _planning.build_behavior_ir_from_knowledge_asset = (
        build_behavior_ir_with_semantic_operation_bindings
    )
    setattr(_planning, _INSTALL_MARKER, True)


def build_discovery_plan(inputs: Any, campaign_handle: Any) -> Any:
    """Bind the immutable scan context for the duration of one planning call."""
    effective_inputs = _planning_inputs_with_declared_adapters(inputs)
    token = bind_scan_ui_contract_context(
        _dict(getattr(effective_inputs, "campaign_context", {}))
    )
    try:
        return _planning.build_discovery_plan(effective_inputs, campaign_handle)
    finally:
        reset_scan_ui_contract_context(token)


__all__ = [
    "build_behavior_ir_with_semantic_operation_bindings",
    "build_discovery_plan",
]
