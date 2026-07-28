"""Deterministic rendering preconditions for visual baseline comparison.

A pixel comparison is trustworthy only when its renderer profile, scroll origin
and font readiness are source-declared and observed. This guard pins the current
formal capability to Chromium CSS-pixel screenshots, waits for ``document.fonts``
and resets the page to document origin before current-image capture.
"""
from __future__ import annotations

import copy
from typing import Any

from . import professional_ui_visual_baseline as _visual

RENDERER_PROFILE = "chromium_css_scale_v1"
SCROLL_ORIGIN = "document_start"
FONT_READINESS = "document_fonts_ready"
_INSTALL_MARKER = "_qualibug_visual_determinism_guard_installed"
_ORIGINAL_VALIDATOR = "_qualibug_visual_validator_before_determinism"
_ORIGINAL_EXECUTOR = "_qualibug_visual_executor_before_determinism"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _determinism_fields() -> dict[str, Any]:
    return {
        "renderer_profile": RENDERER_PROFILE,
        "scroll_origin": SCROLL_ORIGIN,
        "font_readiness": FONT_READINESS,
        "rendering_preconditions_observed": True,
    }


def _update_last_observation(fields: dict[str, Any]) -> None:
    rows = [copy.deepcopy(row) for row in _visual._OBSERVATIONS.get()]
    if not rows:
        return
    rows[-1].update(copy.deepcopy(fields))
    _visual._OBSERVATIONS.set(rows)


def _append_precondition_failure(reason: str, step: dict[str, Any]) -> None:
    _visual._append_observation({
        "expectation": _visual.ACTION,
        "comparison_method": _visual.COMPARISON_METHOD,
        "baseline_scope": _visual.BASELINE_SCOPE,
        "baseline_ref_fingerprint": _visual._fingerprint(
            _text(step.get("baseline_ref"))
        ),
        "declared_baseline_sha256": _text(
            step.get("baseline_sha256")
        ).lower(),
        "status": "INDETERMINATE",
        "reason_code": reason,
        "renderer_profile": RENDERER_PROFILE,
        "scroll_origin": SCROLL_ORIGIN,
        "font_readiness": FONT_READINESS,
        "rendering_preconditions_observed": False,
        "raw_pixels_in_receipt": False,
        "ai_visual_judgement_used": False,
        "baseline_auto_updated": False,
    })


def install_visual_determinism_guard() -> None:
    if getattr(_visual, _INSTALL_MARKER, False):
        return
    original_validate = getattr(
        _visual,
        _ORIGINAL_VALIDATOR,
        _visual._validate_visual_step,
    )
    original_execute = getattr(
        _visual,
        _ORIGINAL_EXECUTOR,
        _visual._execute_visual_baseline,
    )
    setattr(_visual, _ORIGINAL_VALIDATOR, original_validate)
    setattr(_visual, _ORIGINAL_EXECUTOR, original_execute)

    def validate_deterministic_step(raw: dict[str, Any]) -> None:
        original_validate(raw)
        if _text(raw.get("renderer_profile")) != RENDERER_PROFILE:
            raise _visual._professional._browser.BrowserExecutionError(
                "browser_visual_renderer_profile_invalid"
            )
        if _text(raw.get("scroll_origin")) != SCROLL_ORIGIN:
            raise _visual._professional._browser.BrowserExecutionError(
                "browser_visual_scroll_origin_invalid"
            )
        if _text(raw.get("font_readiness")) != FONT_READINESS:
            raise _visual._professional._browser.BrowserExecutionError(
                "browser_visual_font_readiness_invalid"
            )

    def execute_deterministic(page: Any, step: dict[str, Any]) -> dict[str, Any]:
        try:
            observed = page.evaluate(
                """
                async () => {
                  if (!document.fonts || !document.fonts.ready) {
                    return {ready_state: document.readyState, font_status: "unsupported"};
                  }
                  await document.fonts.ready;
                  window.scrollTo(0, 0);
                  return {
                    ready_state: document.readyState,
                    font_status: document.fonts.status,
                    scroll_x: window.scrollX,
                    scroll_y: window.scrollY
                  };
                }
                """
            )
        except Exception as exc:
            reason = "UI_VISUAL_RENDERING_PRECONDITION_FAILED"
            _append_precondition_failure(reason, step)
            raise _visual.VisualBaselineObservationError(reason) from exc
        row = observed if isinstance(observed, dict) else {}
        if _text(row.get("ready_state")) not in {"interactive", "complete"}:
            reason = "UI_VISUAL_DOCUMENT_NOT_READY"
            _append_precondition_failure(reason, step)
            raise _visual.VisualBaselineObservationError(reason)
        if _text(row.get("font_status")) != "loaded":
            reason = "UI_VISUAL_FONTS_NOT_READY"
            _append_precondition_failure(reason, step)
            raise _visual.VisualBaselineObservationError(reason)
        if int(row.get("scroll_x") or 0) != 0 or int(row.get("scroll_y") or 0) != 0:
            reason = "UI_VISUAL_SCROLL_ORIGIN_NOT_STABLE"
            _append_precondition_failure(reason, step)
            raise _visual.VisualBaselineObservationError(reason)
        try:
            receipt = original_execute(page, step)
        except Exception:
            _update_last_observation(_determinism_fields())
            raise
        receipt.update(_determinism_fields())
        _update_last_observation(_determinism_fields())
        return receipt

    _visual._validate_visual_step = validate_deterministic_step
    _visual._execute_visual_baseline = execute_deterministic
    setattr(_visual, _INSTALL_MARKER, True)


__all__ = [
    "FONT_READINESS",
    "RENDERER_PROFILE",
    "SCROLL_ORIGIN",
    "install_visual_determinism_guard",
]
