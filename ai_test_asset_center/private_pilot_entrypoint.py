from __future__ import annotations

"""Deployment entrypoint for the patched private pilot HTTP service.

This module is the canonical executable entrypoint for local and Docker private
pilot deployments. It composes runtime patches before delegating to the legacy
HTTP server.
"""

from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_browser_bridge import (
    install_browser_ui_smoke_patch as _install_browser_ui_smoke_patch,
    restore_browser_ui_smoke_patch,
)
from ai_test_asset_center.private_pilot_credentials_patch import (
    install_service_credentials_patch,
    restore_service_credentials_patch,
)
from ai_test_asset_center.private_pilot_customer_report_patch import (
    install_customer_report_patch as _install_customer_report_patch,
    restore_customer_report_patch,
)
from ai_test_asset_center.private_pilot_deployment_patch import (
    health_payload as _health_payload_impl,
    install_deployment_contract_patch as _install_deployment_contract_patch,
    restore_deployment_contract_patch as _restore_deployment_contract_patch,
)
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch

PATCH_SOURCE = "ai_test_asset_center.private_pilot_entrypoint"


def _health_payload(handler: Any) -> dict[str, Any]:
    return _health_payload_impl(handler, patch_source=PATCH_SOURCE, fallback_root=_service._root())


def install_browser_ui_smoke_patch() -> None:
    _install_browser_ui_smoke_patch(patch_source=PATCH_SOURCE)


def install_customer_report_patch() -> None:
    _install_customer_report_patch(patch_source=PATCH_SOURCE)


def install_deployment_contract_patch() -> None:
    _install_deployment_contract_patch(patch_source=PATCH_SOURCE, fallback_root=_service._root())


def install_extracted_credential_safety_patch() -> None:
    """Move credential handlers from the legacy wrapper to the extracted module.

    The legacy customer-delivery wrapper still installs a credential guard while
    it wires scan campaign context. We restore that handler pair, then install
    the extracted credential patch so production entrypoints use the smaller
    module without requiring a risky full rewrite of private_pilot_server.py.
    """
    restore_service_credentials_patch()
    install_service_credentials_patch(patch_source=PATCH_SOURCE)


def restore_deployment_contract_patch() -> None:
    _restore_deployment_contract_patch()
    restore_browser_ui_smoke_patch()
    restore_customer_report_patch()
    restore_service_credentials_patch()


def install_runtime_patches() -> None:
    install_customer_delivery_gate_patch()
    install_extracted_credential_safety_patch()
    install_browser_ui_smoke_patch()
    install_customer_report_patch()
    install_deployment_contract_patch()


def run_server() -> None:
    install_runtime_patches()
    _service.run_server()


if __name__ == "__main__":
    run_server()
