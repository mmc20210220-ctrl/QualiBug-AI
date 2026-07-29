from __future__ import annotations

"""Scan campaign-context runtime-support installer.

Campaign-context binding is first-class in ``scan()``, continuous discovery,
and ``ScanHandlersMixin``. This module records compatibility status and installs
small additive transport bridges that preserve explicit source contracts in the
same pure campaign-context authority.
"""

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.performance_scan_context_bridge import (
    install_performance_scan_context_bridge,
    restore_performance_scan_context_bridge,
)
from ai_test_asset_center.private_pilot_scan_context_contract import (
    CONTINUOUS_CAMPAIGN_CONTEXTS,
)
from ai_test_asset_center.private_pilot_upload_fixture_health_patch import (
    install_upload_fixture_health_patch,
)
from ai_test_asset_center.private_pilot_upload_fixture_routes import (
    install_private_pilot_upload_fixture_routes,
)
from ai_test_asset_center.private_pilot_upload_fixture_scan_gate import (
    install_upload_fixture_scan_gate,
)
from ai_test_asset_center.ui_upload_fixture_registry_integrity import (
    install_upload_fixture_registry_integrity,
)
from ai_test_asset_center.ui_upload_fixture_runtime_binding import (
    install_ui_upload_fixture_runtime_binding,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_scan_context_patch"


def restore_scan_campaign_context_patch() -> None:
    """Clear campaign-context runtime-support status and additive bridges."""
    restore_performance_scan_context_bridge()
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_START = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = None  # type: ignore[attr-defined]
    CONTINUOUS_CAMPAIGN_CONTEXTS.clear()


def install_scan_campaign_context_patch(*, patch_source: str) -> None:
    """Mark first-class binding active and preserve governed scan contracts."""
    install_performance_scan_context_bridge()
    # Upload fixtures are part of the same immutable scan-context authority. These
    # installers are idempotent and perform no browser or target I/O.
    install_upload_fixture_registry_integrity()
    install_ui_upload_fixture_runtime_binding()
    install_upload_fixture_scan_gate()
    install_private_pilot_upload_fixture_routes()
    install_upload_fixture_health_patch()
    if getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False):
        return
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = True  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    _service._ORIGINAL_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_START = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = None  # type: ignore[attr-defined]
