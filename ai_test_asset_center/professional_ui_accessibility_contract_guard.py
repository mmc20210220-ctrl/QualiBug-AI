"""Require explicit source authority for accessibility rule execution."""
from __future__ import annotations

from typing import Any

from . import professional_ui_accessibility_engine as _engine
from . import professional_ui_readonly as _professional

CUSTOM_STANDARD = "source-declared-rule-set"
_INSTALL_MARKER = "_qualibug_accessibility_contract_guard_installed"
_ORIGINAL_VALIDATE = "_qualibug_accessibility_validate_before_contract_guard"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def install_professional_ui_accessibility_contract_guard() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    original = getattr(
        _engine,
        _ORIGINAL_VALIDATE,
        _engine._validate_step,
    )
    setattr(_engine, _ORIGINAL_VALIDATE, original)

    def validate_with_explicit_authority(raw: dict[str, Any]) -> None:
        declared_standard = _text(raw.get("standard"), limit=80).lower()
        declared_rules = [
            _text(value, limit=100).lower()
            for value in _list(raw.get("rules"))
            if _text(value, limit=100)
        ]
        if not declared_standard and not declared_rules:
            raise _professional._browser.BrowserExecutionError(
                "browser_accessibility_standard_or_rules_missing"
            )
        custom = bool(declared_rules and not declared_standard)
        if custom:
            raw["standard"] = _engine.STANDARD
        original(raw)
        if custom:
            raw["standard"] = CUSTOM_STANDARD

    _engine.CUSTOM_STANDARD = CUSTOM_STANDARD
    _engine._validate_step = validate_with_explicit_authority
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = [
    "CUSTOM_STANDARD",
    "install_professional_ui_accessibility_contract_guard",
]
