from __future__ import annotations

"""Browser UI smoke bridge for private-pilot scans.

Registers a named ``scan_post_hooks`` hook instead of replacing ``__main__.scan``.
"""

import os
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_scan_context_contract import current_scan_campaign_context
from ai_test_asset_center.scan_post_hooks import register_scan_post_hook


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


def scan_base_url_from_context(kwargs: dict[str, Any] | None = None) -> str:
    context = kwargs if isinstance(kwargs, dict) else {}
    for key in ("ui_base_url", "base_url"):
        value = str(context.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    pending = current_scan_campaign_context()
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


def _browser_ui_smoke_hook(result: dict[str, Any], *, project: str, root: Path) -> dict[str, Any]:
    from ai_test_asset_center.browser_ui_smoke import attach_browser_ui_health

    base_url = scan_base_url_from_context({})
    try:
        return attach_browser_ui_health(result, project=project, root=root, base_url=base_url)
    except Exception as exc:
        updated = dict(result)
        updated["browser_ui_health"] = browser_ui_error_report(exc)
        return updated


def install_browser_ui_smoke_patch(*, patch_source: str) -> None:
    """Register non-blocking browser UI smoke evidence as a scan post-hook."""
    if getattr(_service, "_BROWSER_UI_SMOKE_PATCHED", False):
        return
    register_scan_post_hook("browser_ui_smoke", _browser_ui_smoke_hook)
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = None  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = True  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_MODE = "first_class_hook"  # type: ignore[attr-defined]


def restore_browser_ui_smoke_patch() -> None:
    register_scan_post_hook("browser_ui_smoke", None)
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = None  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = False  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    if hasattr(_service, "_BROWSER_UI_SMOKE_MODE"):
        delattr(_service, "_BROWSER_UI_SMOKE_MODE")
