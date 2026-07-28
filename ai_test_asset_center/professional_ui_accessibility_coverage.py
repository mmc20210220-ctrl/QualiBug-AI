"""Project deterministic accessibility rule coverage from formal UI receipts."""
from __future__ import annotations

import copy
import sys
from collections import Counter
from typing import Any

from . import professional_ui_coverage_projection as _coverage
from .formal_ui_surface import EVIDENCE_KEY, OBSERVER_ID, RISK_FAMILY
from .professional_ui_accessibility_engine import (
    ACTION,
    RULE_CATALOG,
    SCHEMA_VERSION,
    STANDARD,
    WCAG_VERSION,
)

_INSTALL_MARKER = "_qualibug_accessibility_coverage_installed"
_ORIGINAL_BUILDER = "_qualibug_professional_coverage_before_accessibility_rules"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _obligations(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(_dict(result.get("test_obligations")).get("obligations"))
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]


def _declared_steps(result: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for obligation in _obligations(result):
        request = _dict(_dict(obligation.get("property")).get("ui_request"))
        plan = _dict(request.get("browser_plan"))
        output.extend(
            dict(row)
            for row in _list(plan.get("steps"))
            if isinstance(row, dict) and _text(row.get("action")).lower() == ACTION
        )
    return output


def _execution_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _dict(_dict(result.get("experiment_execution")).get("results"))
    return [dict(row) for row in rows.values() if isinstance(row, dict)]


def _observations(result: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for execution in _execution_rows(result):
        for receipt in _list(execution.get("observer_receipts")):
            if not isinstance(receipt, dict) or _text(receipt.get("observer_id")) != OBSERVER_ID:
                continue
            ui_evidence = _dict(_dict(receipt.get("evidence")).get(EVIDENCE_KEY))
            output.extend(
                copy.deepcopy(row)
                for row in _list(ui_evidence.get("accessibility_rule_observations"))
                if isinstance(row, dict)
            )
    return output


def _projection(result: dict[str, Any]) -> dict[str, Any]:
    steps = _declared_steps(result)
    observations = _observations(result)
    declared_rules: Counter[str] = Counter()
    declared_standards: Counter[str] = Counter()
    complete_required_count = 0
    exclusion_contract_count = 0
    for step in steps:
        declared_standards[_text(step.get("standard")) or STANDARD] += 1
        complete_required_count += int(step.get("require_complete_scan", True) is True)
        exclusion_contract_count += int(bool(_list(step.get("exclude_selectors"))))
        for rule in _list(step.get("rules")):
            if _text(rule):
                declared_rules[_text(rule)] += 1

    status_counts: Counter[str] = Counter()
    violation_rule_counts: Counter[str] = Counter()
    violation_impact_counts: Counter[str] = Counter()
    violation_wcag_counts: Counter[str] = Counter()
    untestable_rule_counts: Counter[str] = Counter()
    untestable_reason_counts: Counter[str] = Counter()
    complete_count = 0
    incomplete_count = 0
    total_dom_nodes = 0
    evaluated_dom_nodes = 0
    total_focus_candidates = 0
    evaluated_focus_candidates = 0
    ai_judgement_count = 0
    certification_claim_count = 0
    for observation in observations:
        status = _text(observation.get("status")) or "UNKNOWN"
        status_counts[status] += 1
        complete = observation.get("complete_observation") is True
        complete_count += int(complete)
        incomplete_count += int(not complete)
        total_dom_nodes += int(observation.get("dom_node_count") or 0)
        evaluated_dom_nodes += int(observation.get("dom_nodes_evaluated") or 0)
        total_focus_candidates += int(observation.get("keyboard_candidate_count") or 0)
        evaluated_focus_candidates += int(observation.get("keyboard_candidates_evaluated") or 0)
        ai_judgement_count += int(observation.get("ai_accessibility_judgement_used") is True)
        certification_claim_count += int(observation.get("full_wcag_certification_claimed") is True)
        violation_rule_counts.update({
            _text(key): int(value or 0)
            for key, value in _dict(observation.get("violation_counts_by_rule")).items()
            if _text(key)
        })
        violation_impact_counts.update({
            _text(key): int(value or 0)
            for key, value in _dict(observation.get("violation_counts_by_impact")).items()
            if _text(key)
        })
        violation_wcag_counts.update({
            _text(key): int(value or 0)
            for key, value in _dict(observation.get("violation_counts_by_wcag")).items()
            if _text(key)
        })
        untestable_rule_counts.update({
            _text(key): int(value or 0)
            for key, value in _dict(observation.get("untestable_counts_by_rule")).items()
            if _text(key)
        })
        untestable_reason_counts.update({
            _text(key): int(value or 0)
            for key, value in _dict(observation.get("untestable_reason_counts")).items()
            if _text(key)
        })

    catalog = {
        rule: {
            "wcag": meta["wcag"],
            "level": meta["level"],
            "impact": meta["impact"],
        }
        for rule, meta in sorted(RULE_CATALOG.items())
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "wcag_version": WCAG_VERSION,
        "deterministic_standard": STANDARD,
        "rule_catalog": catalog,
        "supported_rule_count": len(catalog),
        "declared_contract_count": len(steps),
        "declared_standard_counts": dict(sorted(declared_standards.items())),
        "declared_rule_counts": dict(sorted(declared_rules.items())),
        "complete_scan_required_contract_count": complete_required_count,
        "source_exclusion_contract_count": exclusion_contract_count,
        "observation_count": len(observations),
        "observation_status_counts": dict(sorted(status_counts.items())),
        "complete_observation_count": complete_count,
        "incomplete_observation_count": incomplete_count,
        "violation_counts_by_rule": dict(sorted(violation_rule_counts.items())),
        "violation_counts_by_impact": dict(sorted(violation_impact_counts.items())),
        "violation_counts_by_wcag": dict(sorted(violation_wcag_counts.items())),
        "untestable_counts_by_rule": dict(sorted(untestable_rule_counts.items())),
        "untestable_reason_counts": dict(sorted(untestable_reason_counts.items())),
        "dom_node_count": total_dom_nodes,
        "dom_nodes_evaluated": evaluated_dom_nodes,
        "keyboard_candidate_count": total_focus_candidates,
        "keyboard_candidates_evaluated": evaluated_focus_candidates,
        "ai_accessibility_judgement_consumed_count": ai_judgement_count,
        "full_wcag_certification_claim_count": certification_claim_count,
        "property_held_requires_complete_observation": True,
        "complex_or_translucent_contrast_is_untestable": True,
        "truncated_scan_is_indeterminate": True,
        "raw_dom_in_receipts": False,
        "raw_page_text_in_receipts": False,
        "full_wcag_certification_claimed": False,
    }


def install_professional_ui_accessibility_coverage() -> None:
    if getattr(_coverage, _INSTALL_MARKER, False):
        return
    original = getattr(
        _coverage,
        _ORIGINAL_BUILDER,
        _coverage.build_professional_ui_coverage,
    )
    setattr(_coverage, _ORIGINAL_BUILDER, original)

    def build_with_accessibility_rules(result: dict[str, Any]) -> dict[str, Any]:
        payload = copy.deepcopy(original(result))
        payload["accessibility_rules"] = _projection(result)
        boundary = _dict(payload.get("capability_boundary"))
        boundary.update({
            "deterministic_accessibility_rule_engine_supported": True,
            "deterministic_accessibility_standard": STANDARD,
            "deterministic_accessibility_wcag_version": WCAG_VERSION,
            "deterministic_accessibility_rule_count": len(RULE_CATALOG),
            "keyboard_focus_traversal_supported": True,
            "focus_visibility_style_delta_observed": True,
            "focus_not_obscured_minimum_observed": True,
            "text_contrast_minimum_observed_when_computable": True,
            "complex_contrast_promoted_to_pass": False,
            "target_size_minimum_spacing_exception_supported": True,
            "accessibility_scan_truncation_promoted_to_pass": False,
            "accessibility_raw_dom_persisted": False,
            "accessibility_ai_opinion_used_as_defect": False,
            "full_accessibility_certification_claimed": False,
        })
        payload["capability_boundary"] = boundary
        return payload

    _coverage.build_professional_ui_coverage = build_with_accessibility_rules
    loss_module = sys.modules.get("ai_test_asset_center.discovery_ui_loss_projection")
    if loss_module is not None and getattr(
        loss_module,
        "build_professional_ui_coverage",
        None,
    ) is original:
        loss_module.build_professional_ui_coverage = build_with_accessibility_rules
    setattr(_coverage, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_accessibility_coverage"]
