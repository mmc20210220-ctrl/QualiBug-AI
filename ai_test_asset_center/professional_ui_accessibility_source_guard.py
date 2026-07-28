"""Reject weakened accessibility standard claims during source admission."""
from __future__ import annotations

from typing import Any

from . import professional_ui_accessibility_engine as _engine
from .enterprise_knowledge_center import _formal_ui_contracts as _contracts

_INSTALL_MARKER = "_qualibug_accessibility_source_guard_installed"
_ORIGINAL_GAPS = "_qualibug_accessibility_gaps_before_source_guard"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _zero_budgets(value: Any) -> bool:
    raw = _dict(value)
    try:
        return all(int(raw.get(impact, 0) or 0) == 0 for impact in _engine.IMPACTS)
    except (TypeError, ValueError):
        return False


def install_professional_ui_accessibility_source_guard() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    original = getattr(
        _contracts,
        _ORIGINAL_GAPS,
        _contracts._expectation_structure_gaps,
    )
    setattr(_contracts, _ORIGINAL_GAPS, original)

    def gaps_with_exact_accessibility_authority(
        expectations: list[dict[str, Any]],
    ) -> list[str]:
        missing = list(original(expectations))
        for index, step in enumerate(expectations, start=1):
            if _text(step.get("action"), limit=100).lower() != _engine.ACTION:
                continue
            prefix = f"{_engine.ACTION}[{index}]"
            standard = _text(step.get("standard"), limit=80).lower()
            rules = [
                _text(value, limit=100).lower()
                for value in _list(step.get("rules"))
                if _text(value, limit=100)
            ]
            if step.get("require_complete_scan", True) is not True:
                missing.append(f"{prefix}.require_complete_scan=true")
            if standard == _engine.STANDARD:
                if rules and tuple(dict.fromkeys(rules)) != tuple(_engine.STANDARD_RULES):
                    missing.append(f"{prefix}.standard_rule_set_exact")
                if _list(step.get("allowed_untestable_rules")):
                    missing.append(f"{prefix}.standard_untestable_waiver_forbidden")
                if _list(step.get("exclude_selectors")):
                    missing.append(f"{prefix}.standard_exclusions_forbidden")
                try:
                    max_violations = int(step.get("max_violations", 0) or 0)
                except (TypeError, ValueError):
                    max_violations = -1
                if max_violations != 0 or not _zero_budgets(step.get("impact_budgets")):
                    missing.append(f"{prefix}.standard_zero_budget_required")
        return list(dict.fromkeys(missing))

    _contracts._expectation_structure_gaps = gaps_with_exact_accessibility_authority
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_accessibility_source_guard"]
