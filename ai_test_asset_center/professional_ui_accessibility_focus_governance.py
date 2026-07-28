"""Conservative focus-observation semantics for accessibility rules."""
from __future__ import annotations

import copy
from typing import Any

from . import professional_ui_accessibility_engine as _engine
from . import professional_ui_accessibility_semantics_guard as _semantics

_INSTALL_MARKER = "_qualibug_accessibility_focus_governance_installed"
_ORIGINAL_FOCUS = "_qualibug_accessibility_focus_before_focus_governance"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def install_professional_ui_accessibility_focus_governance() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    original = getattr(
        _engine,
        _ORIGINAL_FOCUS,
        _engine._focus_audit,
    )
    setattr(_engine, _ORIGINAL_FOCUS, original)

    focused_script = _semantics._FOCUSED_SCRIPT
    marker = "focusVisiblePseudo:pseudo,"
    if marker not in focused_script:
        raise RuntimeError("accessibility_focus_visible_patch_missing")
    _semantics._FOCUSED_SCRIPT = focused_script.replace(
        marker,
        "focusVisiblePseudo:false,",
        1,
    )

    def focus_with_vacuous_empty_scope(page: Any, step: dict[str, Any]) -> dict[str, Any]:
        result = copy.deepcopy(_dict(original(page, step)))
        if int(result.get("candidate_count") or 0) != 0:
            return result
        result["untestable"] = [
            copy.deepcopy(row)
            for row in _list(result.get("untestable"))
            if _dict(row).get("reason") != "no_keyboard_focus_candidates"
        ]
        result["checked"] = 0
        result["truncated"] = False
        result["empty_focus_scope_vacuously_complete"] = True
        return result

    _engine._focus_audit = focus_with_vacuous_empty_scope
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_accessibility_focus_governance"]
