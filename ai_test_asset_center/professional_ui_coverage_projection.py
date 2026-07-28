"""Project professional UI/UX coverage from formal contracts and receipts.

The projection is descriptive, not a second verdict authority. A formal contract
may contain assertions from several dimensions; its attempt/outcome is counted in
each declared dimension so the product can show exactly what was exercised.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .formal_ui_surface import OBSERVER_ID, RISK_FAMILY

CATEGORY_ACTIONS: dict[str, frozenset[str]] = {
    "content_navigation": frozenset({
        "expect_text",
        "expect_url",
        "expect_value",
        "expect_count",
        "expect_attribute",
        "expect_css",
    }),
    "rendered_state": frozenset({
        "expect_visible",
        "expect_hidden",
        "expect_enabled",
        "expect_disabled",
        "expect_checked",
        "expect_unchecked",
    }),
    "accessibility": frozenset({
        "expect_role",
        "expect_accessible_name",
        "expect_accessibility_basics",
    }),
    "layout_responsive": frozenset({
        "expect_dimensions",
        "expect_in_viewport",
        "expect_not_obscured",
        "expect_no_horizontal_overflow",
    }),
    "runtime_quality": frozenset({
        "expect_no_console_errors",
        "expect_no_failed_requests",
    }),
}
CONFIG_ACTIONS = frozenset({"set_viewport", "set_media"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _steps_from_obligation(obligation: dict[str, Any]) -> list[dict[str, Any]]:
    prop = _dict(obligation.get("property"))
    request = _dict(prop.get("ui_request"))
    plan = _dict(request.get("browser_plan"))
    return [
        dict(row)
        for row in _list(plan.get("steps"))
        if isinstance(row, dict) and _text(row.get("action"))
    ]


def _categories(actions: set[str]) -> set[str]:
    categories = {
        category
        for category, supported in CATEGORY_ACTIONS.items()
        if actions & supported
    }
    if actions & CONFIG_ACTIONS:
        categories.add("layout_responsive")
    return categories


def _execution_rows(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = _dict(_dict(result.get("experiment_execution")).get("results"))
    return {
        _text(row.get("obligation_id") or key): dict(row)
        for key, row in rows.items()
        if isinstance(row, dict) and _text(row.get("obligation_id") or key)
    }


def build_professional_ui_coverage(result: dict[str, Any]) -> dict[str, Any]:
    obligation_pack = _dict(result.get("test_obligations"))
    obligations = [
        dict(row)
        for row in _list(obligation_pack.get("obligations"))
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]
    obligation_by_id = {
        _text(row.get("obligation_id")): row
        for row in obligations
        if _text(row.get("obligation_id"))
    }
    attempts = [
        dict(row)
        for row in _list(
            _dict(result.get("obligation_attempt_ledger")).get("attempts")
        )
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]
    executions = _execution_rows(result)

    action_counts: Counter[str] = Counter()
    config_counts: Counter[str] = Counter()
    category_rows = {
        category: {
            "declared_contract_count": 0,
            "selected_contract_count": 0,
            "observed_contract_count": 0,
            "property_held_count": 0,
            "violation_count": 0,
            "deliverable_count": 0,
            "blocked_or_indeterminate_count": 0,
        }
        for category in CATEGORY_ACTIONS
    }
    obligation_categories: dict[str, set[str]] = {}
    for obligation_id, obligation in obligation_by_id.items():
        actions = {
            _text(step.get("action")).lower()
            for step in _steps_from_obligation(obligation)
            if _text(step.get("action"))
        }
        for action in actions:
            if action in CONFIG_ACTIONS:
                config_counts[action] += 1
            else:
                action_counts[action] += 1
        categories = _categories(actions)
        obligation_categories[obligation_id] = categories
        for category in categories:
            category_rows[category]["declared_contract_count"] += 1

    terminal_reason_counts: Counter[str] = Counter()
    for attempt in attempts:
        obligation_id = _text(attempt.get("obligation_id"))
        categories = obligation_categories.get(obligation_id, set())
        execution = _dict(executions.get(obligation_id))
        receipts = [
            row
            for row in _list(execution.get("observer_receipts"))
            if isinstance(row, dict) and _text(row.get("observer_id")) == OBSERVER_ID
        ]
        observed = any(
            _text(row.get("status")).upper() == "OBSERVED" for row in receipts
        )
        oracle_status = _text(
            _dict(execution.get("oracle_verdict")).get("status")
        ).upper()
        deliverable = _text(attempt.get("terminal_status")).upper() == "DELIVERABLE"
        reason = _text(attempt.get("reason_code"))
        if reason and not deliverable:
            terminal_reason_counts[reason] += 1
        for category in categories:
            row = category_rows[category]
            row["selected_contract_count"] += 1
            if observed:
                row["observed_contract_count"] += 1
            if oracle_status == "PROPERTY_HELD":
                row["property_held_count"] += 1
            elif oracle_status == "VIOLATION":
                row["violation_count"] += 1
            if deliverable:
                row["deliverable_count"] += 1
            if not observed or oracle_status not in {"PROPERTY_HELD", "VIOLATION"}:
                row["blocked_or_indeterminate_count"] += 1

    supported_actions = sorted(
        set().union(*CATEGORY_ACTIONS.values()) | CONFIG_ACTIONS
    )
    return {
        "schema_version": "qualibug.professional-ui-coverage.v1",
        "supported_readonly_actions": supported_actions,
        "declared_assertion_action_counts": dict(sorted(action_counts.items())),
        "declared_configuration_action_counts": dict(sorted(config_counts.items())),
        "dimensions": category_rows,
        "dimensions_without_declared_contracts": sorted(
            category
            for category, row in category_rows.items()
            if int(row["declared_contract_count"]) == 0
        ),
        "terminal_reason_counts": dict(sorted(terminal_reason_counts.items())),
        "capability_boundary": {
            "source_declared_contracts_required": True,
            "provider_findings_consumed": False,
            "read_only": True,
            "responsive_viewport_supported": True,
            "media_emulation_supported": True,
            "deterministic_accessibility_basics_supported": True,
            "full_accessibility_certification_claimed": False,
            "visual_baseline_regression_supported": False,
            "controlled_write_interaction_supported": False,
            "cross_browser_matrix_supported": False,
            "ai_usability_opinion_used_as_defect": False,
        },
    }


__all__ = [
    "CATEGORY_ACTIONS",
    "build_professional_ui_coverage",
]
