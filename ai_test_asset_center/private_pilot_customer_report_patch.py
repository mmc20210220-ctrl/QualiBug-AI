from __future__ import annotations

"""Customer-safe report runtime-support status for private-pilot.

Report rendering is first-class in ``PageRenderMixin._render_report_html``.
This module only records compatibility status for health surfaces.
"""

from ai_test_asset_center import private_pilot_service as _service


def install_customer_report_patch(*, patch_source: str) -> None:
    """Mark customer-safe report rendering as installed (handler is first-class)."""
    if getattr(_service, "_CUSTOMER_REPORT_PATCHED", False):
        return
    _service._ORIGINAL_RENDER_REPORT_HTML = None  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCHED = True  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_customer_report_patch() -> None:
    _service._ORIGINAL_RENDER_REPORT_HTML = None  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCHED = False  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
