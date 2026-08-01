"""Install source-bound joins and formal non-HTTP observers on planning.

``discovery_runtime_planning.build_discovery_plan`` resolves its Behavior IR builder and
obligation compiler from module globals at execution time. This compatibility entry replaces
those symbols with stable additive wrappers:

* every declared adapter surface preserved in Behavior IR;
* exact accepted rule/interface identities;
* canonical-field response observers;
* enterprise and explicit scan UI contracts;
* professional source-declared read-only UI/UX assertions;
* responsive viewport/media and deterministic WCAG-oriented accessibility rules;
* immutable deterministic visual baseline regression;
* governed Chromium/Firefox/WebKit desktop and device-profile matrices;
* governed non-production UI interaction with rendered and persistent cleanup equivalence;
* minimized visual and interactive evidence with no HAR or Playwright trace persistence;
* source-declared asynchronous event contracts;
* source-declared sequential read-only latency budgets;
* governed source-declared ASYNC_JOB runtime-integrity contracts;
* one formal experiment mainline for every surface.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from . import discovery_runtime_planning as _planning
from .agent_semantic_linker_authority import (
    enrich_knowledge_asset_with_agent_relationships as _governed_agent_semantic_linker,
)
from .behavior_ir_surface_reconciliation import (
    reconcile_declared_observation_surfaces,
)
from .effect_observer_binding import bind_source_effect_observers
from .formal_event_capability_guard import install_formal_event_capability_guard
from .formal_event_pre_cleanup import install_formal_event_pre_cleanup_observer
from .formal_event_surface import install_formal_event_surface
from .formal_performance_attribution_guard import (
    install_formal_performance_attribution_guard,
)
from .formal_performance_surface import install_formal_performance_surface
from .formal_ui_surface import install_formal_ui_surface
from .formal_ui_surface_guard import install_formal_ui_read_only_guard
from .job_async_protocol import register_job_async_protocol
from .non_http_observers import install_non_http_observers
from .professional_ui_accessibility_contract_guard import (
    install_professional_ui_accessibility_contract_guard,
)
from .professional_ui_accessibility_coverage import (
    install_professional_ui_accessibility_coverage,
)
from .professional_ui_accessibility_engine import (
    install_professional_ui_accessibility_engine,
)
from .professional_ui_accessibility_observation_guard import (
    install_professional_ui_accessibility_observation_guard,
)
from .professional_ui_accessibility_rule_governance import (
    install_professional_ui_accessibility_rule_governance,
)
from .professional_ui_accessibility_semantics_guard import (
    install_professional_ui_accessibility_semantics_guard,
)
from .professional_ui_browser_matrix import (
    install_professional_ui_browser_matrix,
    install_professional_ui_browser_matrix_runtime,
)
from .professional_ui_browser_matrix_coverage import (
    install_professional_ui_browser_matrix_coverage,
)
from .professional_ui_browser_matrix_integrity import (
    install_professional_ui_browser_matrix_integrity,
)
from .professional_ui_contract_guard import install_professional_ui_contract_guard
from .professional_ui_interaction_cleanup import install_controlled_ui_interaction
from .professional_ui_interaction_contract_guard import (
    install_controlled_ui_interaction_contract_guard,
)
from .professional_ui_interaction_privacy_guard import (
    install_controlled_ui_interaction_privacy_guard,
)
from .professional_ui_persistent_cleanup_probe import (
    install_persistent_ui_cleanup_probe,
)
from .professional_ui_readonly import install_professional_ui_readonly
from .professional_ui_responsive_accessibility import (
    install_professional_ui_responsive_accessibility,
)
from .professional_ui_visual_baseline import install_professional_ui_visual_baseline
from .professional_ui_visual_baseline_governance import (
    install_visual_baseline_governance,
)
from .professional_ui_visual_determinism_guard import (
    install_visual_determinism_guard,
)
from .professional_ui_visual_evidence_privacy import (
    install_visual_evidence_privacy,
)
from .professional_ui_visual_image_guard import install_visual_image_guard
from .professional_ui_visual_registry_binding import install_visual_registry_binding
from .professional_ui_visual_viewport_guard import install_visual_viewport_guard
from .scan_event_contract_external_signal import (
    overlay_scan_event_contracts_with_external_signals,
)
from .scan_event_contract_overlay import (
    bind_scan_event_contract_context,
    reset_scan_event_contract_context,
)
from .scan_performance_contract_overlay import (
    bind_scan_performance_contract_context,
    overlay_scan_performance_contracts,
    reset_scan_performance_contract_context,
)
from .scan_ui_contract_overlay import (
    bind_scan_ui_contract_context,
    overlay_scan_ui_contracts,
    reset_scan_ui_contract_context,
)
from .scan_ui_interaction_contract_guard import (
    install_scan_ui_interaction_contract_guard,
)
from .semantic_operation_binding import bind_accepted_semantic_operations
from .source_event_contract_binding import bind_source_event_contracts
from .source_event_obligation_binding import install_source_event_obligation_binding
from .source_job_contract_binding import bind_source_job_contracts
from .source_job_obligation_binding import install_source_job_obligation_binding
from .source_performance_contract_binding import bind_source_performance_contracts
from .source_performance_obligation_binding import (
    install_source_performance_obligation_binding,
)
from .source_ui_contract_binding import bind_source_ui_contracts
from .source_ui_contract_source_guard import install_source_ui_contract_source_guard
from .source_ui_obligation_binding import install_source_ui_obligation_binding
from .source_ui_obligation_compat import install_source_ui_family_vector_compat

_INSTALL_MARKER = "_qualibug_semantic_operation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_behavior_ir_builder"

# Planning resolves this symbol from module globals at execution time. Reuse the mature
# linker while ensuring only governed existing relationships can suppress a new edge.
_planning.enrich_knowledge_asset_with_agent_relationships = (
    _governed_agent_semantic_linker
)

# Register formal surfaces before any obligation or experiment is compiled. Every installer is
# idempotent and performs no target I/O. The UI installers extend one authority in order:
# read-only assertions, responsive configuration, conservative accessibility rule governance,
# explicit source authority, accessibility semantics/evidence, deterministic visual comparison,
# project namespace governance, decode limits and renderer stabilization. Matrix engine selection
# is installed before evidence wrappers so Chromium/Firefox/WebKit receive the same privacy policy.
# Governed interaction, persistent cleanup and visual-registry identity remain unchanged. Matrix
# request expansion is installed last over the final adapter and then its registered observer slot
# is rebound, so no profile can bypass existing validators, accessibility completeness, cleanup or
# Oracle authority. Job registration completes the same risk/protocol registries before a Job
# obligation can be created; it does not install a separate planner.
install_non_http_observers()
install_formal_ui_surface()
install_formal_ui_read_only_guard()
install_professional_ui_readonly()
install_professional_ui_contract_guard()
install_professional_ui_responsive_accessibility()
install_professional_ui_accessibility_engine()
install_professional_ui_accessibility_rule_governance()
install_professional_ui_accessibility_contract_guard()
install_professional_ui_accessibility_semantics_guard()
install_professional_ui_accessibility_observation_guard()
install_professional_ui_visual_baseline()
install_visual_baseline_governance()
install_visual_image_guard()
install_visual_determinism_guard()
install_professional_ui_browser_matrix_runtime()
install_visual_evidence_privacy()
install_controlled_ui_interaction()
install_controlled_ui_interaction_contract_guard()
install_controlled_ui_interaction_privacy_guard()
install_persistent_ui_cleanup_probe()
install_visual_viewport_guard()
install_visual_registry_binding()
install_professional_ui_accessibility_coverage()
install_professional_ui_browser_matrix()
install_professional_ui_browser_matrix_integrity()
install_professional_ui_browser_matrix_coverage()
install_scan_ui_interaction_contract_guard()
install_formal_event_surface()
install_formal_event_capability_guard()
install_formal_event_pre_cleanup_observer()
install_formal_performance_surface()
install_formal_performance_attribution_guard()
install_source_ui_contract_source_guard()
install_source_ui_obligation_binding()
install_source_ui_family_vector_compat()
install_source_event_obligation_binding()
install_source_performance_obligation_binding()
register_job_async_protocol()
install_source_job_obligation_binding()


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
    """Give planning one adapter declaration regardless of which public entry called it."""
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
    """Build canonical IR and apply exact source-grounded joins on every surface."""
    ui_asset, scan_ui_receipt = overlay_scan_ui_contracts(asset)
    event_asset, scan_event_receipt = overlay_scan_event_contracts_with_external_signals(
        ui_asset
    )
    effective_asset, scan_performance_receipt = overlay_scan_performance_contracts(
        event_asset
    )
    behavior_ir = _original_build_behavior_ir(
        effective_asset,
        project_id=project_id,
        source_snapshot_hash=source_snapshot_hash,
        api_operations=api_operations,
        runtime_actors=runtime_actors,
        available_surfaces=available_surfaces,
    )
    behavior_ir, _surface_receipt = reconcile_declared_observation_surfaces(
        behavior_ir,
        available_surfaces,
    )
    semantic_ir, _semantic_receipt = bind_accepted_semantic_operations(
        behavior_ir,
        effective_asset,
    )
    observer_ir, _observer_receipt = bind_source_effect_observers(semantic_ir)
    ui_ir, _ui_receipt = bind_source_ui_contracts(observer_ir, effective_asset)
    event_ir, _event_receipt = bind_source_event_contracts(ui_ir, effective_asset)
    performance_ir, _performance_receipt = bind_source_performance_contracts(
        event_ir,
        effective_asset,
    )
    job_ir, _job_receipt = bind_source_job_contracts(
        performance_ir,
        effective_asset,
    )
    job_ir["scan_ui_contract_overlay_receipt"] = dict(scan_ui_receipt)
    job_ir["scan_event_contract_overlay_receipt"] = dict(scan_event_receipt)
    job_ir["scan_performance_contract_overlay_receipt"] = dict(
        scan_performance_receipt
    )
    return job_ir


if not getattr(_planning, _INSTALL_MARKER, False):
    _planning.build_behavior_ir_from_knowledge_asset = (
        build_behavior_ir_with_semantic_operation_bindings
    )
    setattr(_planning, _INSTALL_MARKER, True)


def build_discovery_plan(inputs: Any, campaign_handle: Any) -> Any:
    """Bind immutable UI, event and performance scan contexts for one planning call."""
    effective_inputs = _planning_inputs_with_declared_adapters(inputs)
    context = _dict(getattr(effective_inputs, "campaign_context", {}))
    ui_token = bind_scan_ui_contract_context(context)
    event_token = bind_scan_event_contract_context(context)
    performance_token = bind_scan_performance_contract_context(context)
    try:
        return _planning.build_discovery_plan(effective_inputs, campaign_handle)
    finally:
        reset_scan_performance_contract_context(performance_token)
        reset_scan_event_contract_context(event_token)
        reset_scan_ui_contract_context(ui_token)


__all__ = [
    "build_behavior_ir_with_semantic_operation_bindings",
    "build_discovery_plan",
]
