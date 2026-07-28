"""Reachability and edge-case guard for professional read-only UI assertions.

The formal scan overlay and source-to-IR binder historically recognized only
expect_text/expect_url. This installer widens both existing authorities to the
professional assertion vocabulary and fixes two semantic boundaries:

* ``expect_hidden`` succeeds when the source locator matches no attached node,
  which is Playwright's hidden-state meaning;
* console ignore expressions are validated before execution, so malformed source
  regex cannot turn a test run into an unclassified runtime failure.
"""
from __future__ import annotations

import re
from typing import Any

from . import professional_ui_readonly as _professional
from . import scan_ui_contract_overlay as _overlay
from . import source_ui_contract_binding as _source_binding

_INSTALL_MARKER = "_qualibug_professional_ui_contract_guard_installed"
_ORIGINAL_EXECUTE = "_qualibug_original_professional_expectation_executor"
_ORIGINAL_VALIDATE_STEP = "_qualibug_original_professional_step_validator"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def install_professional_ui_contract_guard() -> None:
    if getattr(_professional, _INSTALL_MARKER, False):
        return
    original_execute = getattr(
        _professional,
        _ORIGINAL_EXECUTE,
        _professional._execute_expectation,
    )
    original_validate = getattr(
        _professional,
        _ORIGINAL_VALIDATE_STEP,
        _professional._validate_professional_step,
    )
    setattr(_professional, _ORIGINAL_EXECUTE, original_execute)
    setattr(_professional, _ORIGINAL_VALIDATE_STEP, original_validate)

    def validate_with_console_patterns(raw: dict[str, Any], action: str) -> None:
        original_validate(raw, action)
        if action != "expect_no_console_errors":
            return
        for value in _list(_dict(raw).get("ignore_patterns")):
            pattern = _text(value)
            if not pattern:
                raise _professional._browser.BrowserExecutionError(
                    "browser_console_ignore_pattern_empty"
                )
            try:
                re.compile(pattern)
            except re.error as exc:
                raise _professional._browser.BrowserExecutionError(
                    "browser_console_ignore_pattern_invalid"
                ) from exc

    def execute_with_hidden_absence(
        *,
        page: Any,
        step: dict[str, Any],
        console: list[dict[str, Any]],
        network: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if _text(step.get("action")).lower() != "expect_hidden":
            return original_execute(
                page=page,
                step=step,
                console=console,
                network=network,
            )
        locator, strategy = _professional._candidate(page, step)
        count = int(locator.count())
        if count == 0:
            return {
                "expectation": "expect_hidden",
                "source_expectation_fingerprint": _professional._fingerprint(step),
                "raw_observed_value_included": False,
                "locator": {
                    "locator_strategy": strategy,
                    "locator_intent_fingerprint": _professional._fingerprint(
                        step.get("locator_intent") or step.get("selector")
                    ),
                    "matched_count": 0,
                },
                "hidden_by_absence": True,
            }
        return original_execute(
            page=page,
            step=step,
            console=console,
            network=network,
        )

    _professional._validate_professional_step = validate_with_console_patterns
    _professional._execute_expectation = execute_with_hidden_absence
    _overlay._EXPECTATION_ACTIONS = _professional.PROFESSIONAL_EXPECTATIONS
    _source_binding._EXPECTATION_ACTIONS = _professional.PROFESSIONAL_EXPECTATIONS
    setattr(_professional, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_contract_guard"]
