"""Additive health diagnostics for deterministic accessibility execution.

The check never opens a browser or scans a customer page. It distinguishes code
availability from discovery-runtime composition so lazy installation does not rewrite
unrelated API, LLM or platform health conclusions.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any

from . import private_pilot_health_contract as _health

_INSTALL_MARKER = "_qualibug_accessibility_health_patch_installed"
_ORIGINAL_BUILDER = "_qualibug_health_builder_before_accessibility_rules"


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def accessibility_health_status() -> dict[str, Any]:
    module_available = _module_available(
        "ai_test_asset_center.professional_ui_accessibility_engine"
    )
    action_available = False
    source_guard_available = False
    runtime_installed = False
    governance_installed = False
    contract_guard_installed = False
    semantics_guard_installed = False
    observation_guard_installed = False
    exclusion_guard_installed = False
    matrix_guard_installed = False
    coverage_installed = False
    supported_rule_count = 0
    standard_rule_count = 0
    custom_only_rule_count = 0
    try:
        engine = importlib.import_module(
            "ai_test_asset_center.professional_ui_accessibility_engine"
        )
        professional = importlib.import_module(
            "ai_test_asset_center.professional_ui_readonly"
        )
        contracts = importlib.import_module(
            "ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts"
        )
        coverage = importlib.import_module(
            "ai_test_asset_center.professional_ui_coverage_projection"
        )
        action = str(getattr(engine, "ACTION", "") or "")
        action_available = bool(
            action
            and callable(getattr(engine, "install_professional_ui_accessibility_engine", None))
        )
        source_guard_available = callable(
            getattr(contracts, "_expectation_structure_gaps", None)
        )
        runtime_installed = bool(
            getattr(professional, getattr(engine, "_INSTALL_MARKER", ""), False)
        )
        governance_installed = bool(
            getattr(engine, "_qualibug_accessibility_rule_governance_installed", False)
        )
        contract_guard_installed = bool(
            getattr(engine, "_qualibug_accessibility_contract_guard_installed", False)
        )
        semantics_guard_installed = bool(
            getattr(engine, "_qualibug_accessibility_semantics_guard_installed", False)
        )
        observation_guard_installed = bool(
            getattr(engine, "_qualibug_accessibility_observation_guard_installed", False)
        )
        exclusion_guard_installed = bool(
            getattr(engine, "_qualibug_accessibility_exclusion_guard_installed", False)
        )
        matrix_guard_installed = bool(
            getattr(engine, "_qualibug_accessibility_matrix_guard_installed", False)
        )
        coverage_installed = bool(
            getattr(coverage, "_qualibug_accessibility_coverage_installed", False)
        )
        supported_rule_count = len(dict(getattr(engine, "RULE_CATALOG", {}) or {}))
        standard_rule_count = len(tuple(getattr(engine, "STANDARD_RULES", ()) or ()))
        custom_only_rule_count = len(
            frozenset(getattr(engine, "CUSTOM_ONLY_RULES", frozenset()) or frozenset())
        )
    except Exception:
        action_available = False

    code_ready = module_available and action_available and source_guard_available
    governance_composed = all((
        governance_installed,
        contract_guard_installed,
        semantics_guard_installed,
        observation_guard_installed,
        exclusion_guard_installed,
        matrix_guard_installed,
        coverage_installed,
    ))
    status = "healthy" if code_ready else "degraded"
    missing = [
        name
        for name, available in (
            ("accessibility_rule_module", module_available),
            ("accessibility_action", action_available),
            ("accessibility_source_guard", source_guard_available),
        )
        if not available
    ]
    return {
        "schema_version": "qualibug.accessibility-health.v1",
        "status": status,
        "ready": code_ready,
        "missing_components": missing,
        "standard": "wcag22-aa-deterministic",
        "wcag_version": "2.2",
        "supported_rule_count": supported_rule_count,
        "default_standard_rule_count": standard_rule_count,
        "custom_only_rule_count": custom_only_rule_count,
        "checks": {
            "rule_module_available": module_available,
            "action_available": action_available,
            "source_guard_available": source_guard_available,
            "runtime_action_installed": runtime_installed,
            "conservative_rule_governance_installed": governance_installed,
            "explicit_authority_guard_installed": contract_guard_installed,
            "semantics_accuracy_guard_installed": semantics_guard_installed,
            "typed_observation_guard_installed": observation_guard_installed,
            "exclusion_selector_guard_installed": exclusion_guard_installed,
            "matrix_completeness_guard_installed": matrix_guard_installed,
            "coverage_projection_installed": coverage_installed,
        },
        "runtime_installation": (
            "installed"
            if runtime_installed and governance_composed
            else "lazy_on_discovery_runtime_import"
        ),
        "governance": {
            "source_declared_standard_or_rules_required": True,
            "full_standard_zero_budget_required": True,
            "full_standard_exclusions_supported": False,
            "full_standard_untestable_waivers_supported": False,
            "complete_observation_required_for_property_held": True,
            "complex_contrast_promoted_to_pass": False,
            "truncated_scan_promoted_to_pass": False,
            "raw_dom_persisted": False,
            "raw_page_text_persisted": False,
            "ai_accessibility_opinion_used_as_defect": False,
            "full_wcag_certification_claimed": False,
        },
    }


def _rebind_loaded_aliases(old: Any, new: Any) -> None:
    for name in (
        "ai_test_asset_center.private_pilot_deployment_contract",
        "ai_test_asset_center.private_pilot_doctor",
    ):
        module = sys.modules.get(name)
        if module is not None and getattr(
            module,
            "build_private_pilot_health_payload",
            None,
        ) is old:
            module.build_private_pilot_health_payload = new


def install_accessibility_health_patch() -> None:
    if getattr(_health, _INSTALL_MARKER, False):
        return
    original = getattr(
        _health,
        _ORIGINAL_BUILDER,
        _health.build_private_pilot_health_payload,
    )
    setattr(_health, _ORIGINAL_BUILDER, original)

    def build_health_with_accessibility(
        handler: Any,
        *,
        fallback_root: Any,
        patch_source: str,
    ) -> dict[str, Any]:
        payload = original(
            handler,
            fallback_root=fallback_root,
            patch_source=patch_source,
        )
        accessibility = accessibility_health_status()
        payload["accessibility_rules"] = accessibility
        components = payload.get("components")
        if not isinstance(components, dict):
            components = {}
            payload["components"] = components
        components["accessibility_rules"] = {
            "status": accessibility["status"],
            "ready": accessibility["ready"],
            "runtime_installation": accessibility["runtime_installation"],
            "default_standard_rule_count": accessibility[
                "default_standard_rule_count"
            ],
        }
        return payload

    _health.build_private_pilot_health_payload = build_health_with_accessibility
    _rebind_loaded_aliases(original, build_health_with_accessibility)
    setattr(_health, _INSTALL_MARKER, True)


__all__ = [
    "accessibility_health_status",
    "install_accessibility_health_patch",
]
