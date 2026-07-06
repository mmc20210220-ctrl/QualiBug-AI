from __future__ import annotations

"""Browser UI smoke bridge for patched private-pilot scans.

The private pilot entrypoint should remain a thin deployment bootstrapper. This
module owns scan argument resolution and the non-blocking browser UI smoke patch
that attaches browser evidence to scan results.
"""

import os
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_server as _server_patch
from ai_test_asset_center import private_pilot_service as _service


def scan_project_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    value = kwargs.get("project")
    if not value and args:
        value = args[0]
    return str(value or os.environ.get("QUALIBUG_PROJECT") or "real_project_demo")


def scan_root_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Path:
    value = kwargs.get("root")
    if value is None and len(args) >= 2:
        value = args[1]
    try:
        return Path(value).resolve() if value is not None else _service._root()
    except Exception:
        return _service._root()


def scan_base_url_from_context(kwargs: dict[str, Any]) -> str:
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


def browser_ui_error_report(exc: Exception) -> dict[str, Any]:
    return {
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


def install_browser_ui_smoke_patch(*, patch_source: str) -> None:
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
        project = scan_project_from_args(args, kwargs)
        root = scan_root_from_args(args, kwargs)
        base_url = scan_base_url_from_context(kwargs)
        try:
            return attach_browser_ui_health(result, project=project, root=root, base_url=base_url)
        except Exception as exc:
            updated = dict(result)
            updated["browser_ui_health"] = browser_ui_error_report(exc)
            return updated

    scanner_module.scan = _scan_with_browser_ui_smoke
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = original_scan  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = True  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_browser_ui_smoke_patch() -> None:
    original_scan = getattr(_service, "_ORIGINAL_BROWSER_UI_SMOKE_SCAN", None)
    if original_scan is not None:
        from ai_test_asset_center import __main__ as scanner_module

        scanner_module.scan = original_scan
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = None  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = False  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
