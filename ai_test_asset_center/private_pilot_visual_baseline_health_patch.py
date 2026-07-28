"""Additive health component for governed formal visual regression.

The component reports package/runtime readiness without launching a browser,
opening a customer project or requiring any baseline to exist. Project baseline
absence is a contract readiness issue, not global service health.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any

from . import private_pilot_health_contract as _health

_INSTALL_MARKER = "_qualibug_visual_baseline_health_patch_installed"
_ORIGINAL_BUILDER = "_qualibug_health_builder_before_visual_baseline"
_CONSUMER_MODULES = (
    "ai_test_asset_center.private_pilot_deployment_patch",
    "ai_test_asset_center.private_pilot_doctor",
)


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False


def visual_baseline_health_status() -> dict[str, Any]:
    pillow_available = _module_available("PIL.Image")
    playwright_available = _module_available("playwright.sync_api")
    registry_api_available = False
    route_installed = False
    comparison_available = False
    runtime_installed = False
    evidence_policy = "masked_visual_screenshots_no_trace_no_har"
    comparison_method = "pixel_rgba_max_channel_threshold"
    renderer_profile = "chromium_css_scale_v1"
    try:
        registry = importlib.import_module(
            "ai_test_asset_center.visual_baseline_registry"
        )
        registry_api_available = all(
            callable(getattr(registry, name, None))
            for name in (
                "register_visual_baseline",
                "approve_visual_baseline",
                "list_visual_baselines",
                "revoke_visual_baseline",
                "active_visual_baseline_record",
            )
        )
    except Exception:
        registry_api_available = False
    try:
        routing = importlib.import_module(
            "ai_test_asset_center.private_pilot_http_routing"
        )
        route_installed = bool(
            getattr(
                routing.HttpRoutingMixin,
                "_qualibug_visual_baseline_http_patch_installed",
                False,
            )
        )
    except Exception:
        route_installed = False
    try:
        visual = importlib.import_module(
            "ai_test_asset_center.professional_ui_visual_baseline"
        )
        comparison_available = callable(
            getattr(visual, "install_professional_ui_visual_baseline", None)
        )
        comparison_method = str(
            getattr(visual, "COMPARISON_METHOD", comparison_method) or comparison_method
        )
        runtime_installed = bool(
            getattr(
                visual,
                "_qualibug_professional_ui_visual_baseline_installed",
                False,
            )
        )
    except Exception:
        comparison_available = False
    try:
        privacy = importlib.import_module(
            "ai_test_asset_center.professional_ui_visual_evidence_privacy"
        )
        evidence_policy = str(
            getattr(privacy, "EVIDENCE_POLICY", evidence_policy) or evidence_policy
        )
    except Exception:
        pass
    try:
        determinism = importlib.import_module(
            "ai_test_asset_center.professional_ui_visual_determinism_guard"
        )
        renderer_profile = str(
            getattr(determinism, "RENDERER_PROFILE", renderer_profile)
            or renderer_profile
        )
    except Exception:
        pass

    ready = all((
        pillow_available,
        playwright_available,
        registry_api_available,
        route_installed,
        comparison_available,
    ))
    missing = [
        name
        for name, available in (
            ("pillow", pillow_available),
            ("playwright_python", playwright_available),
            ("registry_api", registry_api_available),
            ("http_governance_route", route_installed),
            ("comparison_module", comparison_available),
        )
        if not available
    ]
    return {
        "schema_version": "qualibug.visual-baseline-health.v1",
        "status": "healthy" if ready else "degraded",
        "ready": ready,
        "missing_components": missing,
        "checks": {
            "pillow_available": pillow_available,
            "playwright_python_available": playwright_available,
            "registry_api_available": registry_api_available,
            "http_governance_route_installed": route_installed,
            "formal_comparison_module_available": comparison_available,
            "formal_runtime_currently_installed": runtime_installed,
        },
        "runtime_installation": (
            "installed"
            if runtime_installed
            else "lazy_on_discovery_runtime_import"
        ),
        "renderer_profile": renderer_profile,
        "comparison_method": comparison_method,
        "governance": {
            "active_registry_identity_required": True,
            "source_or_approved_namespace_required": True,
            "baseline_auto_update_supported": False,
            "ai_visual_judgement_used": False,
        },
        "evidence_policy": {
            "name": evidence_policy,
            "har_persisted": False,
            "trace_persisted": False,
            "raw_console_text_persisted": False,
            "raw_network_url_persisted": False,
            "persisted_screenshots_masked_before_first_write": True,
        },
    }


def _propagate_loaded_consumer_aliases(
    original: Any,
    wrapped: Any,
) -> None:
    """Replace only stale aliases that still point at the exact old builder.

    Modules imported after installation receive the wrapped function naturally.
    Modules imported earlier may have copied the old object with ``from ...
    import``; those aliases must be updated or doctor/deployment health diverges
    from the live HTTP route. Unrelated monkeypatches or custom wrappers are not
    overwritten because identity must match ``original`` exactly.
    """
    for module_name in _CONSUMER_MODULES:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        if getattr(module, "build_private_pilot_health_payload", None) is original:
            setattr(module, "build_private_pilot_health_payload", wrapped)


def install_visual_baseline_health_patch() -> None:
    if getattr(_health, _INSTALL_MARKER, False):
        current = _health.build_private_pilot_health_payload
        original = getattr(_health, ORIGINAL_BUILDER, None)
        if callable(original):
            _propagate_loaded_consumer_aliases(original, current)
        return
    original = getattr(
        _health,
        ORIGINAL_BUILDER,
        _health.build_private_pilot_health_payload,
    )
    setattr(_health, ORIGINAL_BUILDER, original)

    def build_health_with_visual_baseline(
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
        status = visual_baseline_health_status()
        payload["visual_baseline"] = status
        components = payload.get("components")
        if not isinstance(components, dict):
            components = {}
            payload["components"] = components
        components["visual_baseline"] = {
            "status": status["status"],
            "ready": status["ready"],
            "runtime_installation": status["runtime_installation"],
        }
        # Additive diagnostic only: a missing optional browser dependency should
        # not rewrite API/LLM/system-behavior health into a different verdict.
        return payload

    _health.build_private_pilot_health_payload = build_health_with_visual_baseline
    _propagate_loaded_consumer_aliases(original, build_health_with_visual_baseline)
    setattr(_health, _INSTALL_MARKER, True)


__all__ = [
    "install_visual_baseline_health_patch",
    "visual_baseline_health_status",
]
