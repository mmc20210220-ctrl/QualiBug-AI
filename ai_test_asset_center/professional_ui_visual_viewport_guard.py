"""Plan and runtime viewport binding for deterministic visual comparison.

The exact CSS-pixel viewport is part of a visual baseline identity. This guard
requires each visual expectation to declare ``viewport_width`` and
``viewport_height``, verifies the active preceding ``set_viewport`` in both
read-only and governed-interaction plans, and rechecks Playwright's actual page
viewport immediately before comparison.
"""
from __future__ import annotations

import copy
from typing import Any

from . import professional_ui_interaction_cleanup as _interaction
from . import professional_ui_readonly as _professional
from . import professional_ui_visual_baseline as _visual

_INSTALL_MARKER = "_qualibug_visual_viewport_guard_installed"
_ORIGINAL_VISUAL_VALIDATOR = "_qualibug_visual_validator_before_viewport_guard"
_ORIGINAL_VISUAL_EXECUTOR = "_qualibug_visual_executor_before_viewport_guard"
_ORIGINAL_READONLY_PLAN = "_qualibug_readonly_plan_before_visual_viewport_guard"
_ORIGINAL_BROWSER_PLAN = "_qualibug_browser_plan_before_visual_viewport_guard"
_ORIGINAL_WRITE_PLAN = "_qualibug_write_plan_before_visual_viewport_guard"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dimension(value: Any, *, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool):
        raise _professional._browser.BrowserExecutionError(code)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise _professional._browser.BrowserExecutionError(code) from exc
    if not minimum <= number <= maximum:
        raise _professional._browser.BrowserExecutionError(code)
    return number


def _validate_plan_viewports(plan: dict[str, Any]) -> None:
    active: tuple[int, int] | None = None
    for step in _list(plan.get("steps")):
        if not isinstance(step, dict):
            continue
        action = _text(step.get("action")).lower()
        if action == "set_viewport":
            active = (int(step["width"]), int(step["height"]))
            continue
        if action != _visual.ACTION:
            continue
        expected = (
            int(step["viewport_width"]),
            int(step["viewport_height"]),
        )
        if active is None:
            raise _professional._browser.BrowserExecutionError(
                "browser_visual_viewport_configuration_missing"
            )
        if active != expected:
            raise _professional._browser.BrowserExecutionError(
                "browser_visual_viewport_configuration_mismatch"
            )


def _update_last_observation(fields: dict[str, Any]) -> None:
    rows = [copy.deepcopy(row) for row in _visual._OBSERVATIONS.get()]
    if not rows:
        return
    rows[-1].update(copy.deepcopy(fields))
    _visual._OBSERVATIONS.set(rows)


def install_visual_viewport_guard() -> None:
    if getattr(_visual, _INSTALL_MARKER, False):
        return
    original_validate = getattr(
        _visual,
        _ORIGINAL_VISUAL_VALIDATOR,
        _visual._validate_visual_step,
    )
    original_execute = getattr(
        _visual,
        _ORIGINAL_VISUAL_EXECUTOR,
        _visual._execute_visual_baseline,
    )
    original_readonly = getattr(
        _professional,
        _ORIGINAL_READONLY_PLAN,
        _professional.validate_professional_browser_plan,
    )
    original_browser = getattr(
        _professional._browser,
        _ORIGINAL_BROWSER_PLAN,
        _professional._browser.validate_browser_plan,
    )
    original_write = getattr(
        _interaction,
        _ORIGINAL_WRITE_PLAN,
        _interaction._validate_write_plan,
    )
    setattr(_visual, _ORIGINAL_VISUAL_VALIDATOR, original_validate)
    setattr(_visual, _ORIGINAL_VISUAL_EXECUTOR, original_execute)
    setattr(_professional, _ORIGINAL_READONLY_PLAN, original_readonly)
    setattr(_professional._browser, _ORIGINAL_BROWSER_PLAN, original_browser)
    setattr(_interaction, _ORIGINAL_WRITE_PLAN, original_write)

    def validate_visual_with_viewport(raw: dict[str, Any]) -> None:
        original_validate(raw)
        raw["viewport_width"] = _dimension(
            raw.get("viewport_width"),
            minimum=240,
            maximum=7680,
            code="browser_visual_viewport_width_invalid",
        )
        raw["viewport_height"] = _dimension(
            raw.get("viewport_height"),
            minimum=240,
            maximum=4320,
            code="browser_visual_viewport_height_invalid",
        )

    def validate_readonly_with_visual_viewport(
        plan: dict[str, Any],
        runtime_contract: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = original_readonly(plan, runtime_contract)
        _validate_plan_viewports(normalized)
        return normalized

    def validate_browser_with_visual_viewport(
        plan: dict[str, Any],
        runtime_contract: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = original_browser(plan, runtime_contract)
        _validate_plan_viewports(normalized)
        return normalized

    def validate_write_with_visual_viewport(
        plan: dict[str, Any],
        runtime_contract: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = original_write(plan, runtime_contract)
        _validate_plan_viewports(normalized)
        return normalized

    def execute_visual_with_viewport(page: Any, step: dict[str, Any]) -> dict[str, Any]:
        expected = {
            "width": int(step["viewport_width"]),
            "height": int(step["viewport_height"]),
        }
        observed = page.viewport_size
        if not isinstance(observed, dict):
            reason = "UI_VISUAL_VIEWPORT_UNOBSERVABLE"
            _visual._append_observation({
                "expectation": _visual.ACTION,
                "status": "INDETERMINATE",
                "reason_code": reason,
                "declared_viewport": expected,
                "actual_viewport_observed": False,
                "raw_pixels_in_receipt": False,
                "ai_visual_judgement_used": False,
                "baseline_auto_updated": False,
            })
            raise _visual.VisualBaselineObservationError(reason)
        actual = {
            "width": int(observed.get("width") or 0),
            "height": int(observed.get("height") or 0),
        }
        if actual != expected:
            reason = "UI_VISUAL_VIEWPORT_RUNTIME_MISMATCH"
            _visual._append_observation({
                "expectation": _visual.ACTION,
                "status": "INDETERMINATE",
                "reason_code": reason,
                "declared_viewport": expected,
                "actual_viewport": actual,
                "actual_viewport_observed": True,
                "raw_pixels_in_receipt": False,
                "ai_visual_judgement_used": False,
                "baseline_auto_updated": False,
            })
            raise _visual.VisualBaselineObservationError(reason)
        try:
            receipt = original_execute(page, step)
        except Exception:
            _update_last_observation({
                "declared_viewport": expected,
                "actual_viewport": actual,
                "actual_viewport_observed": True,
                "viewport_match": True,
            })
            raise
        receipt.update({
            "declared_viewport": expected,
            "actual_viewport": actual,
            "actual_viewport_observed": True,
            "viewport_match": True,
        })
        _update_last_observation({
            "declared_viewport": expected,
            "actual_viewport": actual,
            "actual_viewport_observed": True,
            "viewport_match": True,
        })
        return receipt

    _visual._validate_visual_step = validate_visual_with_viewport
    _visual._execute_visual_baseline = execute_visual_with_viewport
    _professional.validate_professional_browser_plan = (
        validate_readonly_with_visual_viewport
    )
    # Wrap the currently installed unified browser validator. If governed write
    # support is already installed, its mode dispatcher remains intact.
    _professional._browser.validate_browser_plan = (
        validate_browser_with_visual_viewport
    )
    _interaction._validate_write_plan = validate_write_with_visual_viewport
    setattr(_visual, _INSTALL_MARKER, True)


__all__ = ["install_visual_viewport_guard"]
