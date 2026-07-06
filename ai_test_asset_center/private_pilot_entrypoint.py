from __future__ import annotations

"""Deployment entrypoint for the patched private pilot HTTP service.

This module is the canonical executable entrypoint for local and Docker private
pilot deployments. It installs runtime patches before delegating to the legacy
HTTP server.
"""

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_test_asset_center import private_pilot_server as _server_patch
from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.customer_safe_report import contains_mojibake, render_customer_safe_report_html
from ai_test_asset_center.private_pilot_health_contract import build_private_pilot_health_payload
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.version import CANONICAL_HEALTH_PATH, LEGACY_HEALTH_PATH

PATCH_SOURCE = "ai_test_asset_center.private_pilot_entrypoint"


def _health_payload(handler: Any) -> dict[str, Any]:
    return build_private_pilot_health_payload(
        handler,
        fallback_root=_service._root(),
        patch_source=PATCH_SOURCE,
    )


def _scan_project_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    value = kwargs.get("project")
    if not value and args:
        value = args[0]
    return str(value or os.environ.get("QUALIBUG_PROJECT") or "real_project_demo")


def _scan_root_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Path:
    value = kwargs.get("root")
    if value is None and len(args) >= 2:
        value = args[1]
    try:
        return Path(value).resolve() if value is not None else _service._root()
    except Exception:
        return _service._root()


def _scan_base_url_from_context(kwargs: dict[str, Any]) -> str:
    for key in ("ui_base_url", "base_url"):
        value = str(kwargs.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    try:
        pending = _server_patch._SCAN_CAMPAIGN_CONTEXT.get()  # type: ignore[attr-defined]
    except Exception:
        pending = None
    if isinstance(pending, dict):
        for key in ("ui_base_url", "base_url", "target_url"):
            value = str(pending.get(key) or "").strip()
            if value:
                return value.rstrip("/")
    for env_name in ("QUALIBUG_BROWSER_UI_BASE_URL", "QUALIBUG_TARGET_UI_BASE_URL", "QUALIBUG_TARGET_BASE_URL"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value.rstrip("/")
    return ""


def install_customer_report_patch() -> None:
    """Replace legacy customer report HTML that contained mojibake strings."""
    if getattr(_service, "_CUSTOMER_REPORT_PATCHED", False):
        return
    original_renderer = getattr(_service.PrivatePilotHandler, "_render_report_html")

    def _render_report_html_clean(self: Any, project: str, root: Path) -> Any:
        return self._html(render_customer_safe_report_html(project, root))

    _service.PrivatePilotHandler._render_report_html = _render_report_html_clean
    _service._ORIGINAL_RENDER_REPORT_HTML = original_renderer  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCHED = True  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def restore_customer_report_patch() -> None:
    original_renderer = getattr(_service, "_ORIGINAL_RENDER_REPORT_HTML", None)
    if original_renderer is not None:
        _service.PrivatePilotHandler._render_report_html = original_renderer
    _service._ORIGINAL_RENDER_REPORT_HTML = None  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCHED = False  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCH_SOURCE = ""  # type: ignore[attr-defined]


def install_browser_ui_smoke_patch() -> None:
    """Attach non-blocking browser UI smoke evidence to patched scans."""
    if getattr(_service, "_BROWSER_UI_SMOKE_PATCHED", False):
        return
    from ai_test_asset_center import __main__ as scanner_module
    from ai_test_asset_center.browser_ui_smoke import attach_browser_ui_health

    original_scan = getattr(scanner_module, "scan")

    def _scan_with_browser_ui_smoke(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_scan(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        project = _scan_project_from_args(args, kwargs)
        root = _scan_root_from_args(args, kwargs)
        base_url = _scan_base_url_from_context(kwargs)
        try:
            return attach_browser_ui_health(result, project=project, root=root, base_url=base_url)
        except Exception as exc:
            updated = dict(result)
            updated["browser_ui_health"] = {
                "schema_version": "browser-ui-smoke-v1",
                "enabled": False,
                "status": "error",
                "reason_code": "E_BROWSER_UI_RUNTIME_ERROR",
                "message": str(exc)[:500],
                "page_count": 0,
                "reachable_page_count": 0,
                "console_error_count": 0,
                "network_error_count": 0,
                "screenshot_count": 0,
                "pages": [],
                "evidence_files": [],
            }
            return updated

    scanner_module.scan = _scan_with_browser_ui_smoke
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = original_scan  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = True  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def restore_browser_ui_smoke_patch() -> None:
    original_scan = getattr(_service, "_ORIGINAL_BROWSER_UI_SMOKE_SCAN", None)
    if original_scan is not None:
        from ai_test_asset_center import __main__ as scanner_module

        scanner_module.scan = original_scan
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = None  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = False  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = ""  # type: ignore[attr-defined]


def install_deployment_contract_patch() -> None:
    """Normalize health/version behavior for patched deployments."""
    if getattr(_service, "_DEPLOYMENT_CONTRACT_PATCHED", False):
        return
    original_do_get = getattr(_service.PrivatePilotHandler, "do_GET")

    def _do_get_with_deployment_contract(self: Any) -> Any:
        parsed = urlparse(self.path)
        if parsed.path in {CANONICAL_HEALTH_PATH, LEGACY_HEALTH_PATH}:
            return self._json(_health_payload(self))
        return original_do_get(self)

    _service.PrivatePilotHandler.do_GET = _do_get_with_deployment_contract
    _service._ORIGINAL_DEPLOYMENT_DO_GET = original_do_get  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = True  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def restore_deployment_contract_patch() -> None:
    original_do_get = getattr(_service, "_ORIGINAL_DEPLOYMENT_DO_GET", None)
    if original_do_get is not None:
        _service.PrivatePilotHandler.do_GET = original_do_get
    _service._ORIGINAL_DEPLOYMENT_DO_GET = None  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = False  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    restore_browser_ui_smoke_patch()
    restore_customer_report_patch()


def run_server() -> None:
    install_customer_delivery_gate_patch()
    install_browser_ui_smoke_patch()
    install_customer_report_patch()
    install_deployment_contract_patch()
    _service.run_server()


if __name__ == "__main__":
    run_server()
