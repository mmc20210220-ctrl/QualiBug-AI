"""Additive health diagnostics for governed browser/device matrices.

The check never launches a customer browser or downloads binaries.  It separates
contract/runtime composition readiness from engine-binary launch verification so a
healthy API is not rewritten merely because Firefox/WebKit have not yet been used
on this host.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any

from . import private_pilot_health_contract as _health

_INSTALL_MARKER = "_qualibug_browser_matrix_health_patch_installed"
_ORIGINAL_BUILDER = "_qualibug_health_builder_before_browser_matrix"
ENGINES = ("chromium", "firefox", "webkit")


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def browser_matrix_health_status() -> dict[str, Any]:
    playwright_available = _module_available("playwright.sync_api")
    contract_guard_available = False
    execution_module_available = False
    runtime_installed = False
    adapter_installed = False
    coverage_installed = False
    verdict_guard_installed = False
    launch_guard_installed = False
    try:
        guard = importlib.import_module(
            "ai_test_asset_center.enterprise_knowledge_center."
            "_formal_ui_browser_matrix_guard"
        )
        contract_guard_available = all(
            callable(getattr(guard, name, None))
            for name in ("normalize_browser_matrix", "install_formal_ui_browser_matrix_guard")
        )
    except Exception:
        contract_guard_available = False
    try:
        matrix = importlib.import_module(
            "ai_test_asset_center.professional_ui_browser_matrix"
        )
        execution_module_available = all(
            callable(getattr(matrix, name, None))
            for name in (
                "install_professional_ui_browser_matrix",
                "install_professional_ui_browser_matrix_runtime",
            )
        )
        runtime = importlib.import_module("ai_test_asset_center.auto_browser_setup")
        adapter = importlib.import_module("ai_test_asset_center.ui_execution_adapter")
        coverage = importlib.import_module(
            "ai_test_asset_center.professional_ui_coverage_projection"
        )
        runtime_installed = bool(
            getattr(runtime, "_qualibug_browser_matrix_runtime_installed", False)
        )
        adapter_installed = bool(
            getattr(adapter, "_qualibug_browser_matrix_adapter_installed", False)
        )
        coverage_installed = bool(
            getattr(coverage, "_qualibug_browser_matrix_coverage_installed", False)
        )
        verdict_guard_installed = bool(
            getattr(matrix, "_qualibug_browser_matrix_verdict_guard_installed", False)
        )
        launch_guard_installed = bool(
            getattr(matrix, "_qualibug_browser_matrix_launch_guard_installed", False)
        )
    except Exception:
        execution_module_available = False

    contract_ready = contract_guard_available and execution_module_available
    runtime_composed = all((
        runtime_installed,
        adapter_installed,
        coverage_installed,
        verdict_guard_installed,
        launch_guard_installed,
    ))
    status = "healthy" if playwright_available and contract_ready else "degraded"
    missing = [
        name
        for name, available in (
            ("playwright_python", playwright_available),
            ("matrix_contract_guard", contract_guard_available),
            ("matrix_execution_module", execution_module_available),
        )
        if not available
    ]
    return {
        "schema_version": "qualibug.browser-matrix-health.v1",
        "status": status,
        "ready": playwright_available and contract_ready,
        "missing_components": missing,
        "supported_engines": list(ENGINES),
        "checks": {
            "playwright_python_available": playwright_available,
            "contract_guard_available": contract_guard_available,
            "execution_module_available": execution_module_available,
            "runtime_engine_selector_installed": runtime_installed,
            "matrix_adapter_installed": adapter_installed,
            "coverage_projection_installed": coverage_installed,
            "typed_verdict_guard_installed": verdict_guard_installed,
            "bundled_launch_guard_installed": launch_guard_installed,
        },
        "runtime_installation": "installed" if runtime_composed else "lazy_on_discovery_runtime_import",
        "engine_binary_verification": {
            "status": "not_launched_by_health_check",
            "verified_engines": [],
            "verification_occurs_on_first_profile_execution": True,
            "auto_install_is_bounded": True,
        },
        "governance": {
            "source_declared_profiles_required": True,
            "aggregation_policy": "all_profiles_must_pass",
            "property_held_requires_all_profiles": True,
            "runtime_failure_is_formal_violation": False,
            "system_browser_fallback_supported": False,
            "interactive_matrix_supported": False,
            "cross_engine_visual_baseline_supported": False,
            "provider_findings_consumed": False,
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


def install_browser_matrix_health_patch() -> None:
    if getattr(_health, _INSTALL_MARKER, False):
        return
    original = getattr(
        _health,
        _ORIGINAL_BUILDER,
        _health.build_private_pilot_health_payload,
    )
    setattr(_health, _ORIGINAL_BUILDER, original)

    def build_health_with_browser_matrix(
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
        matrix = browser_matrix_health_status()
        payload["browser_matrix"] = matrix
        components = payload.get("components")
        if not isinstance(components, dict):
            components = {}
            payload["components"] = components
        components["browser_matrix"] = {
            "status": matrix["status"],
            "ready": matrix["ready"],
            "runtime_installation": matrix["runtime_installation"],
        }
        return payload

    _health.build_private_pilot_health_payload = build_health_with_browser_matrix
    _rebind_loaded_aliases(original, build_health_with_browser_matrix)
    setattr(_health, _INSTALL_MARKER, True)


__all__ = [
    "browser_matrix_health_status",
    "install_browser_matrix_health_patch",
]
