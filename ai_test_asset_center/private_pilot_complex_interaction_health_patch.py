"""Additive health metadata for governed complex browser interactions."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any

from . import private_pilot_health_contract as _health

_INSTALL_MARKER = "_qualibug_complex_interaction_health_patch_installed"
_ORIGINAL_BUILDER = "_qualibug_health_builder_before_complex_interactions"
_MODULES = (
    "ai_test_asset_center.professional_ui_complex_interactions",
    "ai_test_asset_center.professional_ui_complex_interaction_hardening",
    "ai_test_asset_center.professional_ui_complex_interaction_finalizer",
    "ai_test_asset_center.professional_ui_complex_origin_guard",
    "ai_test_asset_center.enterprise_knowledge_center._formal_ui_complex_interaction_guard",
)


def _available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def complex_interaction_health_status() -> dict[str, Any]:
    modules_available = all(_available(name) for name in _MODULES)
    supported_actions: list[str] = []
    runtime_installed = False
    hardening_installed = False
    finalizer_installed = False
    origin_guard_installed = False
    source_guard_installed = False
    try:
        complex_ui = importlib.import_module(
            "ai_test_asset_center.professional_ui_complex_interactions"
        )
        interaction = importlib.import_module(
            "ai_test_asset_center.professional_ui_interaction_cleanup"
        )
        contracts = importlib.import_module(
            "ai_test_asset_center.enterprise_knowledge_center._formal_ui_contracts"
        )
        supported_actions = sorted(
            str(value)
            for value in frozenset(
                getattr(complex_ui, "COMPLEX_INTERACTIVE_ACTIONS", frozenset())
                or frozenset()
            )
        )
        runtime_installed = bool(
            getattr(interaction, "_qualibug_complex_ui_interactions_installed", False)
        )
        hardening_installed = bool(
            getattr(complex_ui, "_qualibug_complex_interaction_hardening_installed", False)
        )
        finalizer_installed = bool(
            getattr(interaction, "_qualibug_complex_interaction_finalizer_installed", False)
        )
        origin_guard_installed = bool(
            getattr(complex_ui, "_qualibug_complex_origin_guard_installed", False)
        )
        source_guard_installed = bool(
            getattr(contracts, "_qualibug_formal_ui_complex_interaction_guard_installed", False)
        )
    except Exception:
        supported_actions = []

    code_ready = modules_available and set(supported_actions) == {
        "click_download",
        "click_popup",
        "set_input_files",
    }
    composed = all((
        runtime_installed,
        hardening_installed,
        finalizer_installed,
        origin_guard_installed,
        source_guard_installed,
    ))
    return {
        "schema_version": "qualibug.complex-interaction-health.v1",
        "status": "healthy" if code_ready else "degraded",
        "ready": code_ready,
        "supported_actions": supported_actions,
        "runtime_installation": (
            "installed" if composed else "lazy_on_discovery_runtime_import"
        ),
        "checks": {
            "modules_available": modules_available,
            "source_guard_installed": source_guard_installed,
            "runtime_actions_installed": runtime_installed,
            "file_evidence_hardening_installed": hardening_installed,
            "exact_origin_guard_installed": origin_guard_installed,
            "post_persistent_finalizer_installed": finalizer_installed,
        },
        "governance": {
            "approved_sandbox_write_required": True,
            "persistent_cleanup_equivalence_required": True,
            "runtime_file_binding_required": True,
            "literal_upload_paths_supported": False,
            "upload_sha256_required": True,
            "upload_symlink_supported": False,
            "download_raw_content_persisted": False,
            "download_deleted_after_observation": True,
            "popup_source_declared_final_url_required": True,
            "popup_closed_after_observation": True,
            "iframe_exact_approved_origin_required": True,
            "mismatch_promoted_to_formal_violation_v1": False,
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


def install_complex_interaction_health_patch() -> None:
    if getattr(_health, _INSTALL_MARKER, False):
        return
    original = getattr(
        _health,
        _ORIGINAL_BUILDER,
        _health.build_private_pilot_health_payload,
    )
    setattr(_health, _ORIGINAL_BUILDER, original)

    def build_health_with_complex_interactions(
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
        status = complex_interaction_health_status()
        payload["complex_ui_interactions"] = status
        components = payload.get("components")
        if not isinstance(components, dict):
            components = {}
            payload["components"] = components
        components["complex_ui_interactions"] = {
            "status": status["status"],
            "ready": status["ready"],
            "runtime_installation": status["runtime_installation"],
            "supported_action_count": len(status["supported_actions"]),
        }
        return payload

    _health.build_private_pilot_health_payload = build_health_with_complex_interactions
    _rebind_loaded_aliases(original, build_health_with_complex_interactions)
    setattr(_health, _INSTALL_MARKER, True)


__all__ = [
    "complex_interaction_health_status",
    "install_complex_interaction_health_patch",
]
