from __future__ import annotations

"""Patch installer for customer-safe private-pilot report rendering."""

from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.customer_safe_report import render_customer_safe_report_html


def install_customer_report_patch(*, patch_source: str) -> None:
    """Replace legacy customer report HTML that contained mojibake strings."""
    if getattr(_service, "_CUSTOMER_REPORT_PATCHED", False):
        return
    original_renderer = getattr(_service.PrivatePilotHandler, "_render_report_html")

    def _render_report_html_clean(self: Any, project: str, root: Path) -> Any:
        return self._html(render_customer_safe_report_html(project, root))

    _service.PrivatePilotHandler._render_report_html = _render_report_html_clean
    _service._ORIGINAL_RENDER_REPORT_HTML = original_renderer  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCHED = True  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_customer_report_patch() -> None:
    original_renderer = getattr(_service, "_ORIGINAL_RENDER_REPORT_HTML", None)
    if original_renderer is not None:
        _service.PrivatePilotHandler._render_report_html = original_renderer
    _service._ORIGINAL_RENDER_REPORT_HTML = None  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCHED = False  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
