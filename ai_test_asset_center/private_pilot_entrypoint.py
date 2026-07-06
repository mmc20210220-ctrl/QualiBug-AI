from __future__ import annotations

"""Deployment entrypoint for the patched private pilot HTTP service.

This module is the canonical executable entrypoint for local and Docker private
pilot deployments. It installs runtime patches before delegating to the legacy
HTTP server.
"""

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.customer_safe_report import render_customer_safe_report_html
from ai_test_asset_center.private_pilot_browser_bridge import (
    install_browser_ui_smoke_patch as _install_browser_ui_smoke_patch,
    restore_browser_ui_smoke_patch,
)
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


def install_browser_ui_smoke_patch() -> None:
    _install_browser_ui_smoke_patch(patch_source=PATCH_SOURCE)


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
