"""Fail-closed plan-level guards for governed UI interaction.

The responsive UI installer validates ``set_viewport`` and ``set_media`` through
its professional step validator. Controlled interaction has its own phased plan
validator, so these configuration steps must be revalidated there as well. They
are restricted to setup so before/after cleanup probes are compared under one
stable viewport and media environment.
"""
from __future__ import annotations

from typing import Any

from . import professional_ui_interaction_cleanup as _interaction
from . import professional_ui_readonly as _professional

_INSTALL_MARKER = "_qualibug_controlled_ui_interaction_contract_guard_installed"
_ORIGINAL_WRITE_VALIDATOR = (
    "_qualibug_original_controlled_ui_write_validator_before_contract_guard"
)
_CONFIG_ACTIONS = frozenset({"set_viewport", "set_media"})


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def install_controlled_ui_interaction_contract_guard() -> None:
    if getattr(_interaction, _INSTALL_MARKER, False):
        return
    original = getattr(
        _interaction,
        ORIGINAL_WRITE_VALIDATOR,
        _interaction._validate_write_plan,
    )
    setattr(_interaction, ORIGINAL_WRITE_VALIDATOR, original)

    def validate_guarded_write_plan(
        plan: dict[str, Any],
        runtime_contract: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = original(plan, runtime_contract)
        for step in _list(normalized.get("steps")):
            if not isinstance(step, dict):
                continue
            action = _text(step.get("action")).lower()
            if action not in _CONFIG_ACTIONS:
                continue
            if _text(step.get("phase")).lower() != "setup":
                raise _interaction._browser.BrowserExecutionError(
                    f"browser_responsive_configuration_phase_invalid:{action}"
                )
            # The responsive/accessibility installer owns these field rules.
            _professional._validate_professional_step(step, action)
        return normalized

    _interaction._validate_write_plan = validate_guarded_write_plan
    setattr(_interaction, _INSTALL_MARKER, True)


__all__ = ["install_controlled_ui_interaction_contract_guard"]
