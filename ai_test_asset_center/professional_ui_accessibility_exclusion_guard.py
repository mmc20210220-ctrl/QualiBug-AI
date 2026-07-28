"""Fail source-declared accessibility exclusion selectors closed.

Custom accessibility contracts may exclude explicitly governed third-party DOM
regions. An invalid selector must not be silently ignored, because that can turn
excluded content into a false formal defect. Validation happens in the rendered
page; only selector fingerprints and counts enter evidence.
"""
from __future__ import annotations

import copy
from typing import Any

from . import professional_ui_accessibility_engine as _engine
from . import professional_ui_accessibility_observation_guard as _observation

_INSTALL_MARKER = "_qualibug_accessibility_exclusion_guard_installed"
_ORIGINAL_EXECUTE = "_qualibug_accessibility_execute_before_exclusion_guard"


def _invalid_selector_count(page: Any, selectors: list[str]) -> int:
    if not selectors:
        return 0
    return int(page.evaluate(
        """selectors => selectors.reduce((count, selector) => {
          try { document.querySelectorAll(selector); return count; }
          catch (_) { return count + 1; }
        }, 0)""",
        selectors,
    ) or 0)


def _indeterminate_receipt(step: dict[str, Any], *, reason: str, count: int) -> dict[str, Any]:
    receipt = {
        "schema_version": _engine.SCHEMA_VERSION,
        "status": "INDETERMINATE",
        "reason_code": reason,
        "expectation": _engine.ACTION,
        "standard": str(step.get("standard") or ""),
        "wcag_version": _engine.WCAG_VERSION,
        "rules": list(step.get("rules") or []),
        "rule_count": len(list(step.get("rules") or [])),
        "violation_count": 0,
        "violation_counts_by_rule": {},
        "violation_counts_by_impact": {},
        "violation_counts_by_wcag": {},
        "violation_fingerprints": [],
        "finding_list_truncated": False,
        "untestable_counts_by_rule": {
            rule: max(1, count)
            for rule in list(step.get("rules") or [])
        },
        "untestable_reason_counts": {reason: max(1, count)},
        "allowed_untestable_rules": list(step.get("allowed_untestable_rules") or []),
        "complete_observation": False,
        "dom_node_count": 0,
        "dom_nodes_evaluated": 0,
        "keyboard_candidate_count": 0,
        "keyboard_candidates_evaluated": 0,
        "max_violations": int(step.get("max_violations") or 0),
        "impact_budgets": copy.deepcopy(step.get("impact_budgets") or {}),
        "impact_budgets_exceeded": [],
        "exclude_selector_fingerprints": [
            _engine._fingerprint(value)
            for value in list(step.get("exclude_selectors") or [])
        ],
        "raw_exclusion_selectors_included": False,
        "raw_dom_included": False,
        "raw_page_text_included": False,
        "raw_accessible_names_included": False,
        "ai_accessibility_judgement_used": False,
        "full_wcag_certification_claimed": False,
    }
    _observation._append_observation(receipt)
    return receipt


def install_professional_ui_accessibility_exclusion_guard() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    original = getattr(
        _engine,
        _ORIGINAL_EXECUTE,
        _engine._execute_engine,
    )
    setattr(_engine, _ORIGINAL_EXECUTE, original)

    def execute_with_validated_exclusions(page: Any, step: dict[str, Any]) -> dict[str, Any]:
        selectors = [str(value) for value in list(step.get("exclude_selectors") or [])]
        if not selectors:
            return original(page, step)
        try:
            invalid_count = _invalid_selector_count(page, selectors)
        except Exception:
            return _indeterminate_receipt(
                step,
                reason="UI_ACCESSIBILITY_EXCLUSION_VALIDATION_FAILED",
                count=len(selectors),
            )
        if invalid_count:
            return _indeterminate_receipt(
                step,
                reason="UI_ACCESSIBILITY_EXCLUSION_SELECTOR_INVALID",
                count=invalid_count,
            )
        return original(page, step)

    _engine._execute_engine = execute_with_validated_exclusions
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = [
    "install_professional_ui_accessibility_exclusion_guard",
]
