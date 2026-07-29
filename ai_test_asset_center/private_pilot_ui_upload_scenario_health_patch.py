"""Additive health metadata for governed UI upload scenario authority."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any

from . import private_pilot_health_contract as _health

_INSTALL_MARKER = "_qualibug_upload_scenario_health_patch_installed"
_ORIGINAL_BUILDER = "_qualibug_health_builder_before_upload_scenarios"
_MODULES = (
    "ai_test_asset_center.ui_upload_scenario_registry",
    "ai_test_asset_center.ui_upload_scenario_source_authority",
    "ai_test_asset_center.ui_upload_scenario_submission_authority",
    "ai_test_asset_center.ui_upload_scenario_semantic_authority",
    "ai_test_asset_center.ui_upload_scenario_runtime_binding",
    "ai_test_asset_center.private_pilot_ui_upload_scenario_routes",
    "ai_test_asset_center.private_pilot_ui_upload_scenario_scan_gate",
)


def _available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def upload_scenario_health_status() -> dict[str, Any]:
    modules_available = all(_available(name) for name in _MODULES)
    registry_api_available = False
    source_authority_installed = False
    submission_authority_installed = False
    semantic_authority_installed = False
    runtime_binding_installed = False
    routes_installed = False
    scan_gate_installed = False
    try:
        registry = importlib.import_module(
            "ai_test_asset_center.ui_upload_scenario_registry"
        )
        scan_prep = importlib.import_module(
            "ai_test_asset_center.private_pilot_scan_prep"
        )
        scan_handlers = importlib.import_module(
            "ai_test_asset_center.private_pilot_scan_handlers"
        )
        routing = importlib.import_module(
            "ai_test_asset_center.private_pilot_http_routing"
        )
        registry_api_available = all(
            callable(getattr(registry, name, None))
            for name in (
                "register_upload_scenario",
                "approve_upload_scenario",
                "revoke_upload_scenario",
                "list_upload_scenarios",
                "approved_upload_scenario",
                "materialize_upload_scenarios",
            )
        )
        source_authority_installed = bool(
            getattr(
                registry,
                "_qualibug_upload_scenario_source_authority_installed",
                False,
            )
        )
        submission_authority_installed = bool(
            getattr(
                registry,
                "_qualibug_upload_scenario_submission_authority_installed",
                False,
            )
        )
        semantic_authority_installed = bool(
            getattr(
                registry,
                "_qualibug_upload_scenario_semantic_authority_installed",
                False,
            )
        )
        runtime_binding_installed = bool(
            getattr(
                scan_prep,
                "_qualibug_ui_upload_scenario_runtime_binding_installed",
                False,
            )
        )
        routes_installed = bool(
            getattr(
                routing.HttpRoutingMixin,
                "_qualibug_upload_scenario_routes_installed",
                False,
            )
            and getattr(
                routing.HttpRoutingMixin.do_GET,
                "_qualibug_upload_scenario_route_wrapper",
                False,
            )
            and getattr(
                routing.HttpRoutingMixin.do_POST,
                "_qualibug_upload_scenario_route_wrapper",
                False,
            )
        )
        scan_gate_installed = bool(
            getattr(
                scan_handlers.ScanHandlersMixin,
                "_qualibug_upload_scenario_scan_gate_installed",
                False,
            )
        )
    except Exception:
        registry_api_available = False
    code_ready = modules_available and registry_api_available
    composed = all((
        source_authority_installed,
        submission_authority_installed,
        semantic_authority_installed,
        runtime_binding_installed,
        routes_installed,
        scan_gate_installed,
    ))
    return {
        "schema_version": "qualibug.ui-upload-scenario-health.v1",
        "status": "healthy" if code_ready else "degraded",
        "ready": code_ready,
        "runtime_installation": (
            "installed" if composed else "lazy_on_scan_context_install"
        ),
        "checks": {
            "modules_available": modules_available,
            "registry_api_available": registry_api_available,
            "knowledge_source_authority_installed": source_authority_installed,
            "submission_compensation_authority_installed": (
                submission_authority_installed
            ),
            "safe_operation_role_authority_installed": semantic_authority_installed,
            "runtime_binding_installed": runtime_binding_installed,
            "project_routes_installed": routes_installed,
            "typed_scan_gate_installed": scan_gate_installed,
        },
        "governance": {
            "explicit_enterprise_source_required": True,
            "source_version_frozen_at_registration": True,
            "source_version_drift_blocks_execution": True,
            "safe_prerequisite_methods": ["GET", "HEAD", "OPTIONS"],
            "prerequisite_interface_identity_required": True,
            "prerequisite_source_version_frozen": True,
            "write_prerequisite_operation_supported": False,
            "source_declared_actor_role_required": True,
            "caller_authored_behavior_ir_actor_ref_supported": False,
            "approved_fixture_bindings_required": True,
            "fixture_revocation_blocks_execution": True,
            "explicit_submission_mode_required": True,
            "supported_submission_modes": [
                "auto_on_file_selection",
                "click_submit",
            ],
            "click_submit_selector_required": True,
            "business_compensation_selector_required": True,
            "clearing_file_input_is_business_cleanup": False,
            "explicit_approval_required": True,
            "revoked_scenario_can_execute": False,
            "server_builds_contract_from_explicit_fields": True,
            "caller_authored_arbitrary_browser_plan_supported": False,
            "persistent_cleanup_equivalence_required": True,
            "raw_fixture_paths_embedded": False,
            "raw_fixture_content_embedded": False,
            "browser_execution_verified_by_health": False,
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


def install_upload_scenario_health_patch() -> None:
    if getattr(_health, _INSTALL_MARKER, False):
        return
    original = getattr(
        _health,
        _ORIGINAL_BUILDER,
        _health.build_private_pilot_health_payload,
    )
    setattr(_health, _ORIGINAL_BUILDER, original)

    def build_health_with_upload_scenarios(
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
        status = upload_scenario_health_status()
        payload["ui_upload_scenarios"] = status
        components = payload.get("components")
        if not isinstance(components, dict):
            components = {}
            payload["components"] = components
        components["ui_upload_scenarios"] = {
            "status": status["status"],
            "ready": status["ready"],
            "runtime_installation": status["runtime_installation"],
        }
        return payload

    _health.build_private_pilot_health_payload = build_health_with_upload_scenarios
    _rebind_loaded_aliases(original, build_health_with_upload_scenarios)
    setattr(_health, _INSTALL_MARKER, True)


__all__ = [
    "install_upload_scenario_health_patch",
    "upload_scenario_health_status",
]
