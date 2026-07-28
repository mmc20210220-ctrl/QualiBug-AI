"""Additive health metadata for governed UI upload fixture authority."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any

from . import private_pilot_health_contract as _health

_INSTALL_MARKER = "_qualibug_upload_fixture_health_patch_installed"
_ORIGINAL_BUILDER = "_qualibug_health_builder_before_upload_fixtures"
_MODULES = (
    "ai_test_asset_center.ui_upload_fixture_registry",
    "ai_test_asset_center.ui_upload_fixture_registry_integrity",
    "ai_test_asset_center.ui_upload_fixture_ingest",
    "ai_test_asset_center.ui_upload_fixture_runtime_binding",
    "ai_test_asset_center.private_pilot_upload_fixture_routes",
)


def _available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def upload_fixture_health_status() -> dict[str, Any]:
    modules_available = all(_available(name) for name in _MODULES)
    registry_api_available = False
    integrity_installed = False
    runtime_binding_installed = False
    routes_installed = False
    binary_upload_available = False
    campaign_context_binding_installed = False
    runtime_contract_binding_installed = False
    try:
        registry = importlib.import_module(
            "ai_test_asset_center.ui_upload_fixture_registry"
        )
        ingest = importlib.import_module(
            "ai_test_asset_center.ui_upload_fixture_ingest"
        )
        scan_prep = importlib.import_module(
            "ai_test_asset_center.private_pilot_scan_prep"
        )
        scan_context = importlib.import_module(
            "ai_test_asset_center.private_pilot_scan_context_contract"
        )
        pipeline_runtime = importlib.import_module(
            "ai_test_asset_center.pipeline_runtime"
        )
        routing = importlib.import_module(
            "ai_test_asset_center.private_pilot_http_routing"
        )
        registry_api_available = all(
            callable(getattr(registry, name, None))
            for name in (
                "register_upload_fixture",
                "approve_upload_fixture",
                "revoke_upload_fixture",
                "list_upload_fixtures",
                "approved_upload_fixture_binding",
                "materialize_upload_fixture_bindings",
            )
        )
        integrity_installed = bool(
            getattr(
                registry,
                "_qualibug_upload_fixture_registry_integrity_installed",
                False,
            )
        )
        binary_upload_available = callable(
            getattr(ingest, "stage_and_register_upload_fixture", None)
        )
        runtime_binding_installed = bool(
            getattr(
                scan_prep,
                "_qualibug_ui_upload_fixture_runtime_binding_installed",
                False,
            )
        )
        routes_installed = bool(
            getattr(
                routing.HttpRoutingMixin,
                "_qualibug_upload_fixture_routes_installed",
                False,
            )
            and getattr(
                routing.HttpRoutingMixin.do_GET,
                "_qualibug_upload_fixture_route_wrapper",
                False,
            )
            and getattr(
                routing.HttpRoutingMixin.do_POST,
                "_qualibug_upload_fixture_route_wrapper",
                False,
            )
        )
        campaign_context_binding_installed = (
            getattr(
                scan_context.build_campaign_context_from_scan_body,
                "__name__",
                "",
            )
            == "campaign_context_with_upload_fixture_bindings"
        )
        runtime_contract_binding_installed = (
            getattr(pipeline_runtime._runtime_contract, "__name__", "")
            == "runtime_contract_with_upload_fixture_bindings"
        )
    except Exception:
        registry_api_available = False

    code_ready = modules_available and registry_api_available and binary_upload_available
    composed = all((
        integrity_installed,
        runtime_binding_installed,
        routes_installed,
        campaign_context_binding_installed,
        runtime_contract_binding_installed,
    ))
    return {
        "schema_version": "qualibug.ui-upload-fixture-health.v1",
        "status": "healthy" if code_ready else "degraded",
        "ready": code_ready,
        "runtime_installation": (
            "installed" if composed else "lazy_on_scan_context_install"
        ),
        "checks": {
            "modules_available": modules_available,
            "registry_api_available": registry_api_available,
            "binary_upload_available": binary_upload_available,
            "approval_generation_integrity_installed": integrity_installed,
            "project_routes_installed": routes_installed,
            "scan_prepare_binding_installed": runtime_binding_installed,
            "campaign_context_binding_installed": campaign_context_binding_installed,
            "runtime_contract_binding_installed": runtime_contract_binding_installed,
        },
        "governance": {
            "project_scoped_candidate_namespace": True,
            "explicit_approval_required": True,
            "approved_copy_runtime_only": True,
            "sha256_verified_on_register_approve_and_execute": True,
            "source_and_approved_symlinks_supported": False,
            "caller_authored_absolute_paths_supported": False,
            "revoking_source_cascades_to_approved_copy": True,
            "revoked_binding_ref_can_be_reactivated": False,
            "new_approval_requires_new_binding_ref": True,
            "browser_binary_upload_base64_used": False,
            "browser_binary_upload_max_bytes": 10 * 1024 * 1024,
            "revoked_bytes_retained_for_audit": True,
            "raw_file_bytes_embedded_in_registry": False,
            "raw_source_paths_embedded_in_registry": False,
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


def install_upload_fixture_health_patch() -> None:
    if getattr(_health, _INSTALL_MARKER, False):
        return
    original = getattr(
        _health,
        _ORIGINAL_BUILDER,
        _health.build_private_pilot_health_payload,
    )
    setattr(_health, _ORIGINAL_BUILDER, original)

    def build_health_with_upload_fixtures(
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
        status = upload_fixture_health_status()
        payload["ui_upload_fixtures"] = status
        components = payload.get("components")
        if not isinstance(components, dict):
            components = {}
            payload["components"] = components
        components["ui_upload_fixtures"] = {
            "status": status["status"],
            "ready": status["ready"],
            "runtime_installation": status["runtime_installation"],
        }
        return payload

    _health.build_private_pilot_health_payload = build_health_with_upload_fixtures
    _rebind_loaded_aliases(original, build_health_with_upload_fixtures)
    setattr(_health, _INSTALL_MARKER, True)


__all__ = [
    "install_upload_fixture_health_patch",
    "upload_fixture_health_status",
]
