"""Source-admission rules for deterministic visual baseline UI contracts.

Enterprise UI knowledge ingestion must understand visual baseline contracts even
when the discovery runtime has not been imported yet. This lightweight guard
extends the one formal UI parser without importing Pillow or opening baseline
files. Runtime identity, file bytes and image comparison remain the authority of
``professional_ui_visual_baseline``.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from . import _formal_ui_contracts as _contracts

ACTION = "expect_visual_baseline"
INPUT_PREFIX = "visual_baselines"
APPROVED_PREFIX = "approved_visual_baselines"
RENDERER_PROFILE = "chromium_css_scale_v1"
SCROLL_ORIGIN = "document_start"
FONT_READINESS = "document_fonts_ready"
_INSTALL_MARKER = "_qualibug_formal_ui_visual_baseline_guard_installed"
_ORIGINAL_MARKER = "_qualibug_original_visual_expectation_gaps"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_MASKS = 64
_MAX_CHANNEL_TOLERANCE = 32


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_relative_png(value: Any) -> bool:
    ref = _text(value).replace("\\", "/")
    if not ref:
        return False
    path = PurePosixPath(ref)
    return bool(
        not path.is_absolute()
        and ".." not in path.parts
        and len(path.parts) >= 2
        and path.parts[0] in {INPUT_PREFIX, APPROVED_PREFIX}
        and path.suffix.lower() == ".png"
    )


def _number(value: Any, *, minimum: float, maximum: float) -> bool:
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return minimum <= number <= maximum


def _integer(value: Any, *, minimum: int, maximum: int) -> bool:
    if not _number(value, minimum=minimum, maximum=maximum):
        return False
    return int(float(value)) == float(value)


def _visual_gaps(expectations: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for index, step in enumerate(expectations, start=1):
        if _text(step.get("action")).lower() != ACTION:
            continue
        prefix = f"{ACTION}[{index}]"
        if not _valid_relative_png(step.get("baseline_ref")):
            missing.append(f"{prefix}.browser_visual_baseline_scope_invalid")
        if not _SHA256_RE.fullmatch(_text(step.get("baseline_sha256")).lower()):
            missing.append(f"{prefix}.browser_visual_baseline_sha256_invalid")
        if "max_changed_pixel_ratio" not in step:
            missing.append(
                f"{prefix}.browser_visual_changed_pixel_budget_missing"
            )
        elif not _number(
            step.get("max_changed_pixel_ratio"),
            minimum=0.0,
            maximum=1.0,
        ):
            missing.append(
                f"{prefix}.browser_visual_changed_pixel_budget_invalid"
            )
        if not _integer(
            step.get("channel_tolerance", 0),
            minimum=0,
            maximum=_MAX_CHANNEL_TOLERANCE,
        ):
            missing.append(f"{prefix}.browser_visual_channel_tolerance_invalid")
        if not isinstance(step.get("full_page"), bool):
            missing.append(
                f"{prefix}.browser_visual_full_page_boolean_required"
            )
        if step.get("animations_disabled") is not True:
            missing.append(
                f"{prefix}.browser_visual_animations_must_be_disabled"
            )
        if _text(step.get("renderer_profile")) != RENDERER_PROFILE:
            missing.append(f"{prefix}.browser_visual_renderer_profile_invalid")
        if _text(step.get("scroll_origin")) != SCROLL_ORIGIN:
            missing.append(f"{prefix}.browser_visual_scroll_origin_invalid")
        if _text(step.get("font_readiness")) != FONT_READINESS:
            missing.append(f"{prefix}.browser_visual_font_readiness_invalid")
        selectors = _list(step.get("mask_selectors"))
        if len(selectors) > _MAX_MASKS or any(not _text(row) for row in selectors):
            missing.append(f"{prefix}.browser_visual_mask_selectors_invalid")
        intents = _list(step.get("mask_locator_intents"))
        if len(intents) > _MAX_MASKS or any(
            not isinstance(row, dict) for row in intents
        ):
            missing.append(
                f"{prefix}.browser_visual_mask_locator_intents_invalid"
            )
        regions = _list(step.get("mask_regions"))
        if len(regions) > _MAX_MASKS:
            missing.append(f"{prefix}.browser_visual_mask_limit_exceeded")
        for region in regions:
            row = _dict(region)
            if not row or not all(
                _integer(
                    row.get(key),
                    minimum=0 if key in {"x", "y"} else 1,
                    maximum=100_000,
                )
                for key in ("x", "y", "width", "height")
            ):
                missing.append(f"{prefix}.browser_visual_mask_region_invalid")
                break
        if len(selectors) + len(intents) + len(regions) > _MAX_MASKS:
            missing.append(f"{prefix}.browser_visual_mask_limit_exceeded")
    return missing


def install_formal_ui_visual_baseline_guard() -> None:
    if getattr(_contracts, _INSTALL_MARKER, False):
        return
    original = getattr(
        _contracts,
        _ORIGINAL_MARKER,
        _contracts._expectation_structure_gaps,
    )
    setattr(_contracts, _ORIGINAL_MARKER, original)

    def expectation_gaps_with_visual(
        expectations: list[dict[str, Any]],
    ) -> list[str]:
        return [*original(expectations), *_visual_gaps(expectations)]

    _contracts._EXPECTATION_ACTIONS = frozenset({
        *_contracts._EXPECTATION_ACTIONS,
        ACTION,
    })
    _contracts._ALLOWED_ACTIONS = frozenset({
        *_contracts._ALLOWED_ACTIONS,
        ACTION,
    })
    _contracts._expectation_structure_gaps = expectation_gaps_with_visual
    setattr(_contracts, _INSTALL_MARKER, True)


__all__ = [
    "ACTION",
    "APPROVED_PREFIX",
    "FONT_READINESS",
    "INPUT_PREFIX",
    "RENDERER_PROFILE",
    "SCROLL_ORIGIN",
    "install_formal_ui_visual_baseline_guard",
]
