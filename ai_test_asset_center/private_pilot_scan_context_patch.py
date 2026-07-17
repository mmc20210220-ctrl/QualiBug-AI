from __future__ import annotations

"""Scan campaign-context runtime-support installer.

Campaign-context binding is first-class in ``scan()``, continuous discovery,
and ``ScanHandlersMixin``. This module only records compatibility status for
health surfaces that still expose ``scan_campaign_context_patched``.
"""

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_scan_context_contract import CONTINUOUS_CAMPAIGN_CONTEXTS

PATCH_SOURCE = "ai_test_asset_center.private_pilot_scan_context_patch"


def restore_scan_campaign_context_patch() -> None:
    """Clear campaign-context runtime-support status."""
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_CONTINUOUS_START = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = None  # type: ignore[attr-defined]
    CONTINUOUS_CAMPAIGN_CONTEXTS.clear()


def install_scan_campaign_context_patch(*, patch_source: str) -> None:
    """Mark first-class campaign-context binding as active for health surfaces."""
    if getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False):
        return
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = True  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    _service._ORIGINAL_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_CONTINUOUS_START = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = None  # type: ignore[attr-defined]
