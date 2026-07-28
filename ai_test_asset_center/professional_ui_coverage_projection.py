"""Project professional UI/UX coverage from formal contracts and receipts.

The projection is descriptive, not a second verdict authority. A formal contract
may contain assertions from several dimensions; its attempt/outcome is counted in
each declared dimension so the product can show exactly what was exercised.
Interactive treatment and cleanup steps are counted separately from assertions,
visual comparison status comes only from typed observer receipts, and cleanup
equivalence remains mandatory before interactive Oracle or delivery outcomes.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .formal_ui_surface import EVIDENCE_KEY, OBSERVER_ID, RISK_FAMILY
from .professional_ui_interaction_cleanup import INTERACTIVE_ACTIONS
from .professional_ui_interaction_privacy_guard import EVIDENCE_POLICY
from .professional_ui_persistent_cleanup_probe import (
    EQUIVALENCE_SCOPE,
    PERSISTENT_PROBE_PROPERTY,
)
from .professional_ui_visual_baseline import (
    ACTION as VISUAL_ACTION,
    BASELINE_SCOPE as VISUAL_BASELINE_SCOPE,
    COMPARISON_METHOD as VISUAL_COMPARISON_METHOD,
)
from .professional_ui_visual_baseline_governance import (
    APPROVED_PREFIX as VISUAL_APPROVED_PREFIX,
    INPUT_PREFIX as VISUAL_INPUT_PREFIX,
)

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
    "visual_regression": frozenset({VISUAL_ACTION}),
    "runtime_quality": frozenset({
        "expect_no_console_errors",
        "expect_no_failed_requests",
    }),
    "workflow_interaction": INTERACTIVE_ACTIONS,
}
CONFIG_ACTIONS = frozenset({"set_viewport", "set_media"})
ASSERTION_ACTIONS = frozenset(
    set().union(
        *(
            actions
            for category, actions in CATEGORY_ACTIONS.items()
            if category != "workflow_interaction"
        )
    )
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _plan_from_obligation(obligation: dict[str, Any]) -> dict[str, Any]:
    prop = _dict(obligation.get("property"))
    request = _dict(prop.get("ui_request"))
    return _dict(request.get("browser_plan"))


def _steps_from_obligation(obligation: dict[str, Any]) -> list[dict[str, Any]]:
    plan = _plan_from_obligation(obligation)
    return [
        dict(row)
        for row in _list(plan.get("steps"))
        if isinstance(row, dict) and _text(row.get("action"))
    ]


def _persistent_probes_from_obligation(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(_plan_from_obligation(obligation).get("state_probes"))
        if isinstance(row, dict)
        and _text(row.get("property")).lower() == PERSISTENT_PROBE_PROPERTY
    ]


def _visual_steps_from_obligation(
    obligation: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        row
        for row in _steps_from_obligation(obligation)
        if _text(row.get("action")).lower() == VISUAL_ACTION
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


def _ui_receipts(execution: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(execution).get("observer_receipts"))
        if isinstance(row, dict) and _text(row.get("observer_id")) == OBSERVER_ID
    ]


def _cleanup_status(receipts: list[dict[str, Any]]) -> str:
    statuses = []
    for receipt in receipts:
        evidence = _dict(_dict(receipt.get("evidence")).get(EVIDENCE_KEY))
        cleanup = _dict(evidence.get("cleanup_receipt"))
        status = _text(cleanup.get("status")).upper()
        if status:
            statuses.append(status)
    unique = list(dict.fromkeys(statuses))
    return unique[0] if len(unique) == 1 else "AMBIGUOUS" if unique else ""


def _visual_observations(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for receipt in receipts:
        ui_evidence = _dict(_dict(receipt.get("evidence")).get(EVIDENCE_KEY))
        output.extend(
            dict(row)
            for row in _list(ui_evidence.get("visual_baseline_observations"))
            if isinstance(row, dict)
        )
    return output


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

    assertion_counts: Counter[str] = Counter()
    config_counts: Counter[str] = Counter()
    treatment_interaction_counts: Counter[str] = Counter()
    cleanup_interaction_counts: Counter[str] = Counter()
    interaction_contract_count = 0
    persistent_probe_count = 0
    interaction_without_persistent_probe_count = 0
    visual_contract_count = 0
    visual_baseline_ref_counts: Counter[str] = Counter()
    category_rows = {
        category: {
            "declared_contract_count": 0,
            "selected_contract_count": 0,
            "observed_contract_count": 0,
            "property_held_count": 0,
            "violation_count": 0,
            "deliverable_count": 0,
            "blocked_or_indeterminate_count": 0,
            "cleanup_equivalence_accepted_count": 0,
            "cleanup_equivalence_indeterminate_count": 0,
        }
        for category in CATEGORY_ACTIONS
    }
    obligation_categories: dict[str, set[str]] = {}
    obligation_requires_cleanup: dict[str, bool] = {}
    for obligation_id, obligation in obligation_by_id.items():
        steps = _steps_from_obligation(obligation)
        actions = {
            _text(step.get("action")).lower()
            for step in steps
            if _text(step.get("action"))
        }
        for step in steps:
            action = _text(step.get("action")).lower()
            phase = _text(step.get("phase")).lower()
            if action in CONFIG_ACTIONS:
                config_counts[action] += 1
            elif action in INTERACTIVE_ACTIONS:
                if phase == "cleanup":
                    cleanup_interaction_counts[action] += 1
                else:
                    treatment_interaction_counts[action] += 1
            elif action in ASSERTION_ACTIONS:
                assertion_counts[action] += 1
        visual_steps = _visual_steps_from_obligation(obligation)
        if visual_steps:
            visual_contract_count += 1
            for step in visual_steps:
                ref = _text(step.get("baseline_ref"))
                if ref.startswith(VISUAL_INPUT_PREFIX + "/"):
                    visual_baseline_ref_counts[VISUAL_INPUT_PREFIX] += 1
                elif ref.startswith(VISUAL_APPROVED_PREFIX + "/"):
                    visual_baseline_ref_counts[VISUAL_APPROVED_PREFIX] += 1
                else:
                    visual_baseline_ref_counts["invalid_or_legacy_scope"] += 1
        categories = _categories(actions)
        obligation_categories[obligation_id] = categories
        cleanup_authority = _dict(
            _dict(obligation.get("property")).get("ui_cleanup_authority")
        )
        is_interactive = bool(actions & INTERACTIVE_ACTIONS)
        obligation_requires_cleanup[obligation_id] = (
            cleanup_authority.get("equivalence_required") is True
            or is_interactive
        )
        if is_interactive:
            interaction_contract_count += 1
            probes = _persistent_probes_from_obligation(obligation)
            persistent_probe_count += len(probes)
            if not probes:
                interaction_without_persistent_probe_count += 1
        for category in categories:
            category_rows[category]["declared_contract_count"] += 1

    terminal_reason_counts: Counter[str] = Counter()
    cleanup_status_counts: Counter[str] = Counter()
    visual_observation_status_counts: Counter[str] = Counter()
    visual_reason_counts: Counter[str] = Counter()
    visual_observation_count = 0
    visual_comparable_count = 0
    visual_ai_judgement_consumed_count = 0
    invalid_deliverable_without_cleanup_count = 0
    invalid_oracle_without_cleanup_count = 0
    for attempt in attempts:
        obligation_id = _text(attempt.get("obligation_id"))
        categories = obligation_categories.get(obligation_id, set())
        execution = _dict(executions.get(obligation_id))
        receipts = _ui_receipts(execution)
        observed = any(
            _text(row.get("status")).upper() == "OBSERVED" for row in receipts
        )
        for observation in _visual_observations(receipts):
            visual_observation_count += 1
            status = _text(observation.get("status")).upper() or "UNKNOWN"
            visual_observation_status_counts[status] += 1
            reason = _text(observation.get("reason_code"))
            if reason:
                visual_reason_counts[reason] += 1
            if observation.get("dimension_match") is True and (
                observation.get("changed_pixel_ratio") is not None
            ):
                visual_comparable_count += 1
            if observation.get("ai_visual_judgement_used") is True:
                visual_ai_judgement_consumed_count += 1
        cleanup_status = _cleanup_status(receipts)
        if cleanup_status:
            cleanup_status_counts[cleanup_status] += 1
        oracle_status = _text(
            _dict(execution.get("oracle_verdict")).get("status")
        ).upper()
        cleanup_required = obligation_requires_cleanup.get(obligation_id, False)
        cleanup_accepted = cleanup_status == "ACCEPTED"
        outcome_allowed = not cleanup_required or cleanup_accepted
        invalid_oracle = bool(
            cleanup_required
            and not cleanup_accepted
            and oracle_status in {"PROPERTY_HELD", "VIOLATION"}
        )
        if invalid_oracle:
            invalid_oracle_without_cleanup_count += 1
            terminal_reason_counts[
                "UI_ORACLE_WITHOUT_CLEANUP_EQUIVALENCE"
            ] += 1
        ledger_deliverable = (
            _text(attempt.get("terminal_status")).upper() == "DELIVERABLE"
        )
        invalid_deliverable = bool(
            ledger_deliverable
            and cleanup_required
            and not cleanup_accepted
        )
        if invalid_deliverable:
            invalid_deliverable_without_cleanup_count += 1
            terminal_reason_counts[
                "UI_DELIVERABLE_WITHOUT_CLEANUP_EQUIVALENCE"
            ] += 1
        deliverable = ledger_deliverable and not invalid_deliverable
        reason = _text(attempt.get("reason_code"))
        if reason and not deliverable:
            terminal_reason_counts[reason] += 1
        for category in categories:
            row = category_rows[category]
            row["selected_contract_count"] += 1
            if observed:
                row["observed_contract_count"] += 1
            if outcome_allowed and oracle_status == "PROPERTY_HELD":
                row["property_held_count"] += 1
            elif outcome_allowed and oracle_status == "VIOLATION":
                row["violation_count"] += 1
            if deliverable:
                row["deliverable_count"] += 1
            if cleanup_required:
                if cleanup_accepted:
                    row["cleanup_equivalence_accepted_count"] += 1
                else:
                    row["cleanup_equivalence_indeterminate_count"] += 1
            if (
                not observed
                or oracle_status not in {"PROPERTY_HELD", "VIOLATION"}
                or invalid_oracle
                or invalid_deliverable
            ):
                row["blocked_or_indeterminate_count"] += 1

    supported_readonly = sorted(ASSERTION_ACTIONS | CONFIG_ACTIONS)
    supported_interactions = sorted(INTERACTIVE_ACTIONS)
    return {
        "schema_version": "qualibug.professional-ui-coverage.v2",
        "supported_readonly_actions": supported_readonly,
        "supported_governed_interaction_actions": supported_interactions,
        "declared_assertion_action_counts": dict(sorted(assertion_counts.items())),
        "declared_configuration_action_counts": dict(sorted(config_counts.items())),
        "declared_treatment_interaction_action_counts": dict(
            sorted(treatment_interaction_counts.items())
        ),
        "declared_cleanup_interaction_action_counts": dict(
            sorted(cleanup_interaction_counts.items())
        ),
        "visual_baseline_contracts": {
            "declared_visual_contract_count": visual_contract_count,
            "declared_baseline_namespace_counts": dict(
                sorted(visual_baseline_ref_counts.items())
            ),
            "visual_observation_count": visual_observation_count,
            "comparable_visual_observation_count": visual_comparable_count,
            "visual_observation_status_counts": dict(
                sorted(visual_observation_status_counts.items())
            ),
            "visual_reason_counts": dict(sorted(visual_reason_counts.items())),
            "ai_visual_judgement_consumed_count": (
                visual_ai_judgement_consumed_count
            ),
            "baseline_scope": VISUAL_BASELINE_SCOPE,
            "comparison_method": VISUAL_COMPARISON_METHOD,
            "allowed_baseline_namespaces": [
                VISUAL_INPUT_PREFIX,
                VISUAL_APPROVED_PREFIX,
            ],
            "baseline_auto_update_supported": False,
        },
        "interaction_cleanup_contracts": {
            "declared_interaction_contract_count": interaction_contract_count,
            "declared_persistent_probe_count": persistent_probe_count,
            "interaction_without_persistent_probe_count": (
                interaction_without_persistent_probe_count
            ),
            "equivalence_scope": EQUIVALENCE_SCOPE,
            "persistent_probe_property": PERSISTENT_PROBE_PROPERTY,
        },
        "cleanup_delivery_invariant": {
            "cleanup_equivalence_required_for_oracle": True,
            "cleanup_equivalence_required_for_delivery": True,
            "invalid_oracle_without_cleanup_count": (
                invalid_oracle_without_cleanup_count
            ),
            "invalid_deliverable_without_cleanup_count": (
                invalid_deliverable_without_cleanup_count
            ),
            "invalid_oracles_counted_as_outcomes": False,
            "invalid_deliverables_counted_as_deliverable": False,
        },
        "dimensions": category_rows,
        "dimensions_without_declared_contracts": sorted(
            category
            for category, row in category_rows.items()
            if int(row["declared_contract_count"]) == 0
        ),
        "cleanup_equivalence_status_counts": dict(
            sorted(cleanup_status_counts.items())
        ),
        "terminal_reason_counts": dict(sorted(terminal_reason_counts.items())),
        "capability_boundary": {
            "source_declared_contracts_required": True,
            "provider_findings_consumed": False,
            "read_only_assertions_supported": True,
            "read_only_only": False,
            "responsive_viewport_supported": True,
            "media_emulation_supported": True,
            "deterministic_accessibility_basics_supported": True,
            "full_accessibility_certification_claimed": False,
            "visual_baseline_regression_supported": True,
            "visual_baseline_sha256_required": True,
            "visual_changed_pixel_budget_required": True,
            "visual_dynamic_region_masking_supported": True,
            "visual_sensitive_region_masking_supported": True,
            "visual_baseline_auto_update_supported": False,
            "visual_provider_or_ai_opinion_used_as_defect": False,
            "visual_allowed_baseline_namespaces": [
                VISUAL_INPUT_PREFIX,
                VISUAL_APPROVED_PREFIX,
            ],
            "controlled_write_interaction_supported": True,
            "approved_nonproduction_target_required": True,
            "production_write_supported": False,
            "browser_cleanup_equivalence_required": True,
            "persistent_cleanup_probe_required": True,
            "rendered_state_only_cleanup_accepted": False,
            "cleanup_equivalence_scope": EQUIVALENCE_SCOPE,
            "persistent_probe_property": PERSISTENT_PROBE_PROPERTY,
            "universal_backend_restoration_claimed": False,
            "cleanup_failure_can_be_oracle_outcome": False,
            "cleanup_failure_can_be_deliverable": False,
            "interaction_evidence_policy": EVIDENCE_POLICY,
            "interactive_har_persisted": False,
            "interactive_trace_persisted": False,
            "runtime_exception_text_persisted": False,
            "cross_browser_matrix_supported": False,
            "ai_usability_opinion_used_as_defect": False,
        },
    }


__all__ = [
    "ASSERTION_ACTIONS",
    "CATEGORY_ACTIONS",
    "build_professional_ui_coverage",
]
