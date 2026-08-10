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

import re
from dataclasses import replace
from typing import Any

from . import discovery_runtime_planning as _planning
from .agent_semantic_linker_authority import (
    AgentSemanticLinkerError,
    RECEIPT_SCHEMA as AGENT_SEMANTIC_LINK_RECEIPT_SCHEMA,
    enrich_knowledge_asset_with_agent_relationships as _governed_agent_semantic_linker,
)
from .behavior_ir_surface_reconciliation import (
    reconcile_declared_observation_surfaces,
)
from .business_behavior_invariant_binding import (
    bind_business_behavior_invariants,
)
from .rule_contract_validation_binding import (
    bind_rule_contract_validation_invariants,
)
from .effect_observer_binding import bind_source_effect_observers
from .formal_event_capability_guard import install_formal_event_capability_guard
from .formal_event_pre_cleanup import install_formal_event_pre_cleanup_observer
from .formal_event_surface import install_formal_event_surface
from .formal_performance_attribution_guard import (
    install_formal_performance_attribution_guard,
)
from .formal_performance_surface import install_formal_performance_surface
from .formal_stability_surface import install_formal_stability_surface
from .formal_ui_surface import install_formal_ui_surface
from .formal_ui_surface_guard import install_formal_ui_read_only_guard
from .job_async_protocol import register_job_async_protocol
from .llm_reasoning import ReasoningConfig
from .message_chain_binding import (
    bind_source_message_chains,
    install_message_chain_obligation_binding,
)
from .message_chain_contract_overlay import (
    bind_message_chain_contract_context,
    overlay_message_chain_contracts_with_external_signals,
    reset_message_chain_contract_context,
)
from .message_chain_surface import (
    install_message_chain_pre_cleanup_observer,
    install_message_chain_surface,
)
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
from .scan_stability_contract_overlay import (
    bind_scan_stability_contract_context,
    overlay_scan_stability_contracts,
    reset_scan_stability_contract_context,
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
from .formal_parameter_scale_surface import install_formal_parameter_scale_surface
from .source_parameter_bound_contracts import (
    bind_source_parameter_contracts,
    derive_parameter_bound_contracts,
    overlay_scan_parameter_contracts,
)
from .source_parameter_obligation_binding import (
    install_source_parameter_obligation_binding,
)
from .source_stability_contract_binding import bind_source_stability_contracts
from .source_stability_obligation_binding import (
    install_source_stability_obligation_binding,
)
from .source_ui_contract_binding import bind_source_ui_contracts
from .source_ui_contract_source_guard import install_source_ui_contract_source_guard
from .source_ui_obligation_binding import install_source_ui_obligation_binding
from .source_ui_obligation_compat import install_source_ui_family_vector_compat

_INSTALL_MARKER = "_qualibug_semantic_operation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_behavior_ir_builder"

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
# Message-chain verification extends the single-hop event chain with
# cross-service semantics (event name / trigger source / consumers / expected
# effects): one new observer, one new assertion kind and one new protocol on
# the same event_delivery_consistency family, plus a pre-cleanup observer so
# the chain-effect state readback happens before cleanup compensation removes
# the correlated entity.
install_message_chain_surface()
install_message_chain_pre_cleanup_observer()
install_formal_performance_surface()
install_formal_performance_attribution_guard()
# The stability installer was never invoked anywhere: its observer, assertion
# kind and risk family stayed unregistered, so stability_reliability defects
# were structurally unreachable even when a source stability contract existed.
# Idempotent registration only; activation still requires the source contract.
install_formal_stability_surface()
# Read-only state-audit protocol: consumes the state_audit_planner's
# audit_mode=read_only obligations (template readonly_audit_validation) which
# previously had no protocol consumer and blocked as
# validation_body_protocol_requires_write_operation.
from .readonly_audit_protocol import install_readonly_audit_protocol

install_readonly_audit_protocol()
install_source_ui_contract_source_guard()
install_source_ui_obligation_binding()
install_source_ui_family_vector_compat()
install_source_event_obligation_binding()
install_message_chain_obligation_binding()
install_source_performance_obligation_binding()
install_source_stability_obligation_binding()
# Parameter-bound performance contracts (REPORT-008 class): the parameter-scale
# surface registers a second protocol on the performance_latency family (one
# observer, one assertion kind) and the obligation wrapper compiles
# parameter-bound invariants plus the generic resource-protection degradation
# channel for unbounded integer query parameters on GET/HEAD operations.
# Idempotent registration only; activation still requires the source contract.
install_formal_parameter_scale_surface()
install_source_parameter_obligation_binding()
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


def _semantic_provider_availability() -> tuple[bool, str]:
    """Return whether the configured reasoning provider can serve semantic links.

    Availability is configuration-only; no provider request is issued here. An
    invalid local provider configuration fails safe to disabled and remains
    visible through the campaign-context basis field.
    """
    try:
        config = ReasoningConfig.from_env()
    except (OSError, TypeError, ValueError) as exc:
        return False, f"provider_config_invalid:{type(exc).__name__}"
    if config.enabled:
        return True, "configured_provider"
    return False, "provider_not_configured"


def _agent_semantic_linker_with_visible_failure(
    knowledge_asset: dict[str, Any],
    *,
    client: Any | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run the single governed linker and preserve provider failures as data.

    Source-integrity failures still raise: evaluator-private content, duplicate
    rule/interface identities and malformed knowledge assets must never be
    converted into a normal degraded scan. Provider unavailability and model
    output failures keep the source-only plan alive with an explicit receipt.
    """
    try:
        return _governed_agent_semantic_linker(
            knowledge_asset,
            client=client,
        )
    except AgentSemanticLinkerError as exc:
        detail = str(exc)
        fatal_prefixes = (
            "evaluator_private_context_forbidden",
            "agent_semantic_duplicate_identity",
            "knowledge_asset_not_object",
        )
        if detail.startswith(fatal_prefixes):
            raise
        status = (
            "NOT_APPLICABLE"
            if detail.startswith("agent_semantic_inputs_empty")
            else "FAILED"
        )
        reason_code = (
            "agent_semantic_inputs_empty"
            if status == "NOT_APPLICABLE"
            else "agent_semantic_linking_failed"
        )
        logger = getattr(_planning, "_planning_logger", None)
        if logger is not None:
            log = logger.warning if status == "NOT_APPLICABLE" else logger.error
            log(
                "agent_semantic_linking_%s %s: %s",
                status.lower(),
                type(exc).__name__,
                detail[:300],
                exc_info=exc if status == "FAILED" else None,
            )
        receipt = {
            "schema_version": AGENT_SEMANTIC_LINK_RECEIPT_SCHEMA,
            "status": status,
            "reason_code": reason_code,
            "error_class": type(exc).__name__,
            "error": detail[:300],
            "accepted_relationship_count": 0,
            "source_asset_preserved": True,
            "semantic_linking_degraded_to_source_only": True,
            "parallel_semantic_linker_created": False,
        }
        # The linker only raises when EVERY unit failed (provider or schema
        # failure); partial failures are merged into the normal receipt. The
        # raised message carries the failed-unit count so the degrade receipt
        # stays granular instead of hiding how many units were lost.
        if detail.startswith("agent_semantic_all_units_failed:"):
            matched = re.search(r":units_failed=(\d+)\s*$", detail)
            if matched is not None:
                receipt["failed_unit_count"] = int(matched.group(1))
        preserved = dict(knowledge_asset) if isinstance(knowledge_asset, dict) else {}
        preserved["agent_semantic_link_receipt"] = receipt
        return preserved, receipt


# Planning resolves this symbol from module globals at execution time. Reuse the
# mature linker and keep provider/model failures visible without creating a
# second semantic mapping authority.
_planning.enrich_knowledge_asset_with_agent_relationships = (
    _agent_semantic_linker_with_visible_failure
)


def _planning_inputs_with_declared_adapters(inputs: Any) -> Any:
    """Give planning one adapter declaration and one semantic-link policy."""
    original_context = _dict(getattr(inputs, "campaign_context", {}))
    context = dict(original_context)
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

    if "agent_semantic_linking_enabled" not in context:
        provider_available, provider_basis = _semantic_provider_availability()
        approved_deep_scan = (
            _text(context.get("execution_mode")) == "approved_sandbox_write"
        )
        context["agent_semantic_linking_enabled"] = bool(
            provider_available and approved_deep_scan
        )
        context["agent_semantic_linking_enablement_basis"] = (
            "auto_enabled_configured_provider_approved_sandbox"
            if provider_available and approved_deep_scan
            else "execution_mode_not_approved_sandbox"
            if provider_available
            else provider_basis
        )
    elif isinstance(context.get("agent_semantic_linking_enabled"), bool):
        context.setdefault(
            "agent_semantic_linking_enablement_basis",
            "explicit_scan_control",
        )

    if context == original_context:
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
    chain_asset, scan_chain_receipt = (
        overlay_message_chain_contracts_with_external_signals(event_asset)
    )
    effective_asset, scan_performance_receipt = overlay_scan_performance_contracts(
        chain_asset
    )
    stability_asset, scan_stability_receipt = overlay_scan_stability_contracts(
        effective_asset
    )
    param_asset, scan_parameter_receipt = overlay_scan_parameter_contracts(
        stability_asset
    )
    # Source-declared parameter bounds (OpenAPI integer query parameter
    # minimum/maximum or verbatim range statements) become parameter-scale
    # performance contracts on GET/HEAD operations.  Extraction only: the
    # parameter name and the declared bounds come verbatim from the source.
    derived_param_asset, param_derive_receipt = derive_parameter_bound_contracts(
        param_asset,
        api_operations=api_operations,
        runtime_actors=runtime_actors,
    )
    # Historical defect documentation (HISTORICAL_BUGS.md / 历史缺陷记录) is
    # visible enterprise material the requirement-doc scoring excludes. This
    # adapter re-admits it as source-backed defect-class rule candidates that
    # flow through the generic rule-contract binding channel. Non-amount
    # classes are recorded as coverage notes, never rules.
    from .enterprise_knowledge_center._common import ROOT as _EKC_ROOT
    from .historical_defect_rule_binding import (
        enrich_asset_with_historical_defect_rules,
    )

    derived_param_asset, _historical_receipt = enrich_asset_with_historical_defect_rules(
        derived_param_asset,
        root=_EKC_ROOT,
        project_id=project_id,
    )
    behavior_ir = _original_build_behavior_ir(
        derived_param_asset,
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
    # CONFIRMED business behaviors with exact-source API bindings become IR
    # invariants here, so business understanding produces obligations instead
    # of staying reference-only. Unbound behaviors remain visible gaps.
    business_ir, _business_behavior_receipt = bind_business_behavior_invariants(
        semantic_ir,
        effective_asset,
    )
    # Source constraint rules whose derived invariants are still unbound
    # (operation_refs == []) become validation-contract invariants here: the
    # rule's subject and constraint vocabulary resolve to the governed
    # entity's declared fields and consuming operations, so the obligation
    # compiler's validation → validation_rejection mapping can fire. Fully
    # data-driven and non-duplicating — rules already bound by the shared
    # subject-frame channel or legacy binding are skipped (receipted).
    rule_contract_ir, _rule_contract_receipt = bind_rule_contract_validation_invariants(
        business_ir,
        effective_asset,
    )
    observer_ir, _observer_receipt = bind_source_effect_observers(rule_contract_ir)
    ui_ir, _ui_receipt = bind_source_ui_contracts(observer_ir, effective_asset)
    event_ir, _event_receipt = bind_source_event_contracts(ui_ir, effective_asset)
    chain_ir, _chain_receipt = bind_source_message_chains(event_ir, effective_asset)
    performance_ir, _performance_receipt = bind_source_performance_contracts(
        chain_ir,
        effective_asset,
    )
    stability_ir, _stability_receipt = bind_source_stability_contracts(
        performance_ir,
        effective_asset,
    )
    parameter_ir, _parameter_scale_receipt = bind_source_parameter_contracts(
        stability_ir,
        derived_param_asset,
    )
    job_ir, _job_receipt = bind_source_job_contracts(
        parameter_ir,
        effective_asset,
    )
    job_ir["scan_ui_contract_overlay_receipt"] = dict(scan_ui_receipt)
    job_ir["scan_event_contract_overlay_receipt"] = dict(scan_event_receipt)
    job_ir["scan_message_chain_contract_overlay_receipt"] = dict(scan_chain_receipt)
    job_ir["scan_performance_contract_overlay_receipt"] = dict(
        scan_performance_receipt
    )
    job_ir["scan_stability_contract_overlay_receipt"] = dict(scan_stability_receipt)
    job_ir["scan_parameter_contract_overlay_receipt"] = dict(scan_parameter_receipt)
    job_ir["parameter_bound_contract_derivation_receipt"] = dict(param_derive_receipt)
    job_ir["source_parameter_contract_binding_receipt"] = dict(_parameter_scale_receipt)
    job_ir["rule_contract_validation_receipt"] = dict(_rule_contract_receipt)
    job_ir["historical_defect_rule_receipt"] = dict(_historical_receipt)
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
    chain_token = bind_message_chain_contract_context(context)
    performance_token = bind_scan_performance_contract_context(context)
    stability_token = bind_scan_stability_contract_context(context)
    try:
        return _planning.build_discovery_plan(effective_inputs, campaign_handle)
    finally:
        reset_scan_stability_contract_context(stability_token)
        reset_scan_performance_contract_context(performance_token)
        reset_message_chain_contract_context(chain_token)
        reset_scan_event_contract_context(event_token)
        reset_scan_ui_contract_context(ui_token)


__all__ = [
    "build_behavior_ir_with_semantic_operation_bindings",
    "build_discovery_plan",
]
