"""Preserve accessibility coverage evidence and fail incomplete scans closed.

The core rule engine computes deterministic findings and untestable coverage.  This
installer keeps that material in a typed observer receipt.  An incomplete scan is
not a UI defect, but it also cannot prove PROPERTY_HELD; the formal observer is
therefore changed to INDETERMINATE unless a separate typed accessibility violation
was already observed.
"""
from __future__ import annotations

import contextvars
import copy
from collections import Counter
from typing import Any

from . import formal_ui_surface as _formal
from . import observer_contracts_base as _observers
from . import professional_ui_accessibility_engine as _engine
from . import professional_ui_readonly as _professional

_INSTALL_MARKER = "_qualibug_accessibility_observation_guard_installed"
_ORIGINAL_EXECUTE = "_qualibug_accessibility_execute_before_observation_guard"
_ORIGINAL_OBSERVER = "_qualibug_accessibility_observer_before_observation_guard"
_OBSERVATIONS: contextvars.ContextVar[list[dict[str, Any]]] = contextvars.ContextVar(
    "qualibug_accessibility_rule_observations",
    default=[],
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _append_observation(receipt: dict[str, Any]) -> None:
    rows = [copy.deepcopy(row) for row in _OBSERVATIONS.get()]
    rows.append(copy.deepcopy(receipt))
    _OBSERVATIONS.set(rows)


def _execute_with_preserved_observation(page: Any, step: dict[str, Any]) -> dict[str, Any]:
    dom = _engine._dom_audit(page, step)
    focus = _engine._focus_audit(page, step)
    findings = [
        _engine._finding_receipt(row)
        for row in [*_list(dom.get("findings")), *_list(focus.get("findings"))]
        if isinstance(row, dict) and _text(row.get("rule")) in _engine.RULE_CATALOG
    ]
    untestable_rows = [
        dict(row)
        for row in [*_list(dom.get("untestable")), *_list(focus.get("untestable"))]
        if isinstance(row, dict) and _text(row.get("rule")) in _engine.RULE_CATALOG
    ]
    rule_counts = Counter(row["rule"] for row in findings)
    impact_counts = Counter(row["impact"] for row in findings)
    wcag_counts = Counter(row["wcag"] for row in findings)
    untestable_counts: Counter[str] = Counter()
    untestable_reason_counts: Counter[str] = Counter()
    for row in untestable_rows:
        count = max(1, int(row.get("count") or 1))
        untestable_counts[_text(row.get("rule"), limit=100)] += count
        untestable_reason_counts[_text(row.get("reason"), limit=120)] += count

    allowed_untestable = set(step["allowed_untestable_rules"])
    disallowed_untestable = {
        rule: count
        for rule, count in untestable_counts.items()
        if rule not in allowed_untestable
    }
    truncated = bool(dom.get("truncated")) or bool(focus.get("truncated"))
    complete = not truncated and not disallowed_untestable
    exceeded_impacts = [
        impact
        for impact, count in impact_counts.items()
        if count > int(step["impact_budgets"].get(impact, 0))
    ]
    budget_exceeded = (
        len(findings) > int(step["max_violations"])
        or bool(exceeded_impacts)
    )
    status = (
        "VIOLATION_OBSERVED"
        if budget_exceeded
        else "INDETERMINATE"
        if step["require_complete_scan"] and not complete
        else "OBSERVED"
    )
    reason_code = (
        "UI_ACCESSIBILITY_VIOLATION_BUDGET_EXCEEDED"
        if budget_exceeded
        else "UI_ACCESSIBILITY_OBSERVATION_INCOMPLETE"
        if status == "INDETERMINATE"
        else ""
    )
    receipt = {
        "schema_version": _engine.SCHEMA_VERSION,
        "status": status,
        "reason_code": reason_code,
        "expectation": _engine.ACTION,
        "standard": step["standard"],
        "wcag_version": _engine.WCAG_VERSION,
        "rules": list(step["rules"]),
        "rule_count": len(step["rules"]),
        "violation_count": len(findings),
        "violation_counts_by_rule": dict(sorted(rule_counts.items())),
        "violation_counts_by_impact": dict(sorted(impact_counts.items())),
        "violation_counts_by_wcag": dict(sorted(wcag_counts.items())),
        "violation_fingerprints": [_engine._fingerprint(row) for row in findings[:500]],
        "finding_list_truncated": len(findings) > 500,
        "untestable_counts_by_rule": dict(sorted(untestable_counts.items())),
        "untestable_reason_counts": dict(sorted(untestable_reason_counts.items())),
        "allowed_untestable_rules": sorted(allowed_untestable),
        "complete_observation": complete,
        "dom_node_count": int(dom.get("total") or 0),
        "dom_nodes_evaluated": int(dom.get("visited") or 0),
        "keyboard_candidate_count": int(focus.get("candidate_count") or 0),
        "keyboard_candidates_evaluated": int(focus.get("checked") or 0),
        "max_violations": int(step["max_violations"]),
        "impact_budgets": copy.deepcopy(step["impact_budgets"]),
        "impact_budgets_exceeded": sorted(exceeded_impacts),
        "exclude_selector_fingerprints": [
            _engine._fingerprint(value) for value in step["exclude_selectors"]
        ],
        "raw_dom_included": False,
        "raw_page_text_included": False,
        "raw_accessible_names_included": False,
        "ai_accessibility_judgement_used": False,
        "full_wcag_certification_claimed": False,
    }
    _append_observation(receipt)
    if budget_exceeded:
        detail = "_".join(sorted(exceeded_impacts))[:100] or "total"
        raise _professional.ProfessionalUIExpectationError(
            _engine.ACTION,
            f"violation_budget_exceeded_{len(findings)}_{detail}",
        )
    return receipt


def install_professional_ui_accessibility_observation_guard() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    original_execute = getattr(
        _engine,
        _ORIGINAL_EXECUTE,
        _engine._execute_engine,
    )
    original_observer = _observers._REGISTERED_OBSERVER_HANDLERS.get(
        _formal.OBSERVER_ID
    )
    if not callable(original_observer):
        raise RuntimeError("formal_ui_observer_handler_missing")
    setattr(_engine, _ORIGINAL_EXECUTE, original_execute)
    setattr(_engine, _ORIGINAL_OBSERVER, original_observer)

    def observer_with_accessibility_receipts(envelope: dict[str, Any]) -> dict[str, Any]:
        token = _OBSERVATIONS.set([])
        try:
            receipt = original_observer(envelope)
            observations = [copy.deepcopy(row) for row in _OBSERVATIONS.get()]
        finally:
            _OBSERVATIONS.reset(token)
        if not observations:
            return receipt
        evidence = copy.deepcopy(_dict(receipt.get("evidence")))
        ui_evidence = copy.deepcopy(_dict(evidence.get(_formal.EVIDENCE_KEY)))
        ui_evidence["accessibility_rule_observations"] = observations
        ui_evidence["accessibility_rule_observation_count"] = len(observations)
        ui_evidence["accessibility_ai_judgement_consumed"] = False
        original_violation = ui_evidence.get("violation_observed") is True
        incomplete = any(
            _text(row.get("status"), limit=80) == "INDETERMINATE"
            for row in observations
        )
        status = _text(receipt.get("status"), limit=40) or "INDETERMINATE"
        reason_code = _text(receipt.get("reason_code"), limit=160)
        if incomplete and not original_violation:
            ui_evidence["expectation_satisfied"] = None
            ui_evidence["violation_observed"] = False
            status = "INDETERMINATE"
            reason_code = "UI_ACCESSIBILITY_OBSERVATION_INCOMPLETE"
        evidence[_formal.EVIDENCE_KEY] = ui_evidence
        return _observers._receipt(
            observer_id=_text(receipt.get("observer_id")) or _formal.OBSERVER_ID,
            status=status,
            reason_code=reason_code,
            evidence=evidence,
            campaign_id=_text(receipt.get("campaign_id")),
            execution_id=_text(receipt.get("execution_id")),
        )

    _engine._execute_engine = _execute_with_preserved_observation
    _formal._ui_observer_handler = observer_with_accessibility_receipts
    _observers._REGISTERED_OBSERVER_HANDLERS[
        _formal.OBSERVER_ID
    ] = observer_with_accessibility_receipts
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_accessibility_observation_guard"]
