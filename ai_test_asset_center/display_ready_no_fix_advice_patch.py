from __future__ import annotations

"""Compatibility installer for display-ready no-fix-advice boundary.

Customer-facing stripping is first-class inside ``display_ready_formatter``.
This module only records runtime-support status for private-pilot health/doctor.
"""

from ai_test_asset_center import display_ready_formatter as _formatter

PATCH_SOURCE = "ai_test_asset_center.display_ready_no_fix_advice_patch"


def install_display_ready_no_fix_advice_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    if getattr(_formatter, "_NO_FIX_ADVICE_DISPLAY_READY_PATCHED", False):
        return
    _formatter._ORIGINAL_BUILD_TECHNICAL_DETAILS_NO_FIX = None  # type: ignore[attr-defined]
    _formatter._ORIGINAL_FORMAT_SINGLE_FINDING_NO_FIX = None  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_PATCHED = True  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_MODE = "first_class"  # type: ignore[attr-defined]


def restore_display_ready_no_fix_advice_patch() -> None:
    _formatter._ORIGINAL_BUILD_TECHNICAL_DETAILS_NO_FIX = None  # type: ignore[attr-defined]
    _formatter._ORIGINAL_FORMAT_SINGLE_FINDING_NO_FIX = None  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_PATCHED = False  # type: ignore[attr-defined]
    _formatter._NO_FIX_ADVICE_DISPLAY_READY_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    if hasattr(_formatter, "_NO_FIX_ADVICE_DISPLAY_READY_MODE"):
        delattr(_formatter, "_NO_FIX_ADVICE_DISPLAY_READY_MODE")
