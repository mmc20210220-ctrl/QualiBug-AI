"""Project formal-UI-specific losses from existing authority receipts.

This module does not invent a second execution metric. It slices the generic
obligation attempt ledger by the registered UI risk family and joins the three
upstream receipts that precede selection:

    scan overlay -> source/IR binding -> UI obligation compiler
    -> compile -> execute -> observe -> Oracle -> Delivery Gate

Professional UI coverage and this top-level funnel share the same cleanup gate:
interactive outcomes are not counted as observed Oracle results or deliverables
unless the typed UI observer carries an ACCEPTED cleanup-equivalence receipt.
Recall, precision and F1 remain unavailable without an external hidden-GT
evaluator.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .formal_ui_surface import EVIDENCE_KEY, OBSERVER_ID, RISK_FAMILY
from .professional_ui_coverage_projection import build_professional_ui_coverage
from .professional_ui_interaction_cleanup import INTERACTIVE_ACTIONS


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _stage_status(attempt: dict[str, Any], stage: str) -> str:
    for row in _list(attempt.get("stages")):
        if isinstance(row, dict) and _text(row.get("stage")) == stage:
            return _text(row.get("status")).upper()
    return ""


def _execution_rows(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = _dict(_dict(result.get("experiment_execution")).get("results"))
    return {
        _text(row.get("obligation_id") or key): dict(row)
        for key, row in raw.items()
        if isinstance(row, dict) and _text(row.get("obligation_id") or key)
    }


def _ui_receipts(execution: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(execution.get("observer_receipts"))
        if isinstance(row, dict) and _text(row.get("observer_id")) == OBSERVER_ID
    ]


def _cleanup_status(receipts: list[dict[str, Any]]) -> str:
    statuses: list[str] = []
    for receipt in receipts:
        ui_evidence = _dict(_dict(receipt.get("evidence")).get(EVIDENCE_KEY))
        status = _text(_dict(ui_evidence.get("cleanup_receipt")).get("status")).upper()
        if status:
            statuses.append(status)
    unique = list(dict.fromkeys(statuses))
    return unique[0] if len(unique) == 1 else "AMBIGUOUS" if unique else ""


def _requires_cleanup(obligation: dict[str, Any]) -> bool:
    prop = _dict(obligation.get("property"))
    authority = _dict(prop.get("ui_cleanup_authority"))
    if authority.get("equivalence_required") is True:
        return True
    request = _dict(prop.get("ui_request"))
    plan = _dict(request.get("browser_plan"))
    return any(
        _text(row.get("action")).lower() in INTERACTIVE_ACTIONS
        for row in _list(plan.get("steps"))
        if isinstance(row, dict)
    )


def build_formal_ui_loss_funnel(result: dict[str, Any]) -> dict[str, Any]:
    behavior_ir = _dict(result.get("behavior_ir"))
    test_obligations = _dict(result.get("test_obligations"))
    overlay = _dict(behavior_ir.get("scan_ui_contract_overlay_receipt"))
    source_binding = _dict(behavior_ir.get("source_ui_contract_binding_receipt"))
    obligation_binding = _dict(test_obligations.get("source_ui_obligation_receipt"))

    ui_obligations = [
        dict(row)
        for row in _list(test_obligations.get("obligations"))
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]
    obligation_by_id = {
        _text(row.get("obligation_id")): row
        for row in ui_obligations
        if _text(row.get("obligation_id"))
    }
    attempts = [
        dict(row)
        for row in _list(_dict(result.get("obligation_attempt_ledger")).get("attempts"))
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]
    execution_by_obligation = _execution_rows(result)

    compiled = sum(1 for row in attempts if _stage_status(row, "compile") == "COMPILED")
    executed = sum(
        1
        for row in attempts
        if _stage_status(row, "execution") in {"EXECUTED", "DELIVERABLE"}
    )
    observed = 0
    oracle_evaluated = 0
    oracle_violation = 0
    oracle_property_held = 0
    deliverable = 0
    invalid_cleanup_oracle_count = 0
    invalid_cleanup_deliverable_count = 0
    observer_reason_counts: Counter[str] = Counter()
    oracle_status_counts: Counter[str] = Counter()
    terminal_reasons: Counter[str] = Counter()

    for attempt in attempts:
        obligation_id = _text(attempt.get("obligation_id"))
        execution = _dict(execution_by_obligation.get(obligation_id))
        receipts = _ui_receipts(execution)
        cleanup_required = _requires_cleanup(_dict(obligation_by_id.get(obligation_id)))
        cleanup_accepted = _cleanup_status(receipts) == "ACCEPTED"
        outcome_allowed = not cleanup_required or cleanup_accepted

        receipt_observed = any(
            _text(row.get("status")).upper() == "OBSERVED" for row in receipts
        )
        if receipt_observed and outcome_allowed:
            observed += 1
        observer_reason_counts.update(
            _text(row.get("reason_code"))
            for row in receipts
            if _text(row.get("status")).upper() != "OBSERVED"
            and _text(row.get("reason_code"))
        )
        if receipt_observed and not outcome_allowed:
            observer_reason_counts["UI_OBSERVED_WITHOUT_CLEANUP_EQUIVALENCE"] += 1

        oracle = _dict(execution.get("oracle_verdict"))
        oracle_status = _text(oracle.get("status")).upper()
        if oracle_status and outcome_allowed:
            oracle_evaluated += 1
            oracle_status_counts[oracle_status] += 1
            if oracle_status == "VIOLATION":
                oracle_violation += 1
            elif oracle_status == "PROPERTY_HELD":
                oracle_property_held += 1
        elif oracle_status in {"VIOLATION", "PROPERTY_HELD"} and not outcome_allowed:
            invalid_cleanup_oracle_count += 1
            oracle_status_counts["SUPPRESSED_WITHOUT_CLEANUP_EQUIVALENCE"] += 1

        ledger_deliverable = _text(attempt.get("terminal_status")).upper() == "DELIVERABLE"
        if ledger_deliverable and outcome_allowed:
            deliverable += 1
        elif ledger_deliverable and not outcome_allowed:
            invalid_cleanup_deliverable_count += 1
            terminal_reasons["UI_DELIVERABLE_WITHOUT_CLEANUP_EQUIVALENCE"] += 1
        else:
            reason = _text(attempt.get("reason_code"))
            if reason:
                terminal_reasons[reason] += 1

    binding_reason_counts = Counter({
        _text(key): _safe_int(value)
        for key, value in _dict(source_binding.get("reason_counts")).items()
        if _text(key)
    })
    skipped_reason_counts = Counter({
        _text(key): _safe_int(value)
        for key, value in _dict(obligation_binding.get("skipped_reason_counts")).items()
        if _text(key)
    })

    formal_candidates = _safe_int(overlay.get("formal_candidate_count"))
    enterprise_contracts = _safe_int(source_binding.get("contract_count"))
    source_contract_count = max(formal_candidates, enterprise_contracts)
    bound_invariants = _safe_int(source_binding.get("bound_invariant_count"))
    obligation_count = len(ui_obligations)
    selected_count = len(attempts)
    professional_coverage = build_professional_ui_coverage(result)

    return {
        "schema_version": "qualibug.formal-ui-loss-funnel.v1",
        "risk_family": RISK_FAMILY,
        "observer_id": OBSERVER_ID,
        "measurement_scope": "formal_ui_contract_conversion_only",
        "stages": [
            {"stage": "source_contract", "count": source_contract_count},
            {"stage": "ir_bound", "count": bound_invariants},
            {"stage": "obligation_generated", "count": obligation_count},
            {"stage": "selected", "count": selected_count},
            {"stage": "compiled", "count": compiled},
            {"stage": "executed", "count": executed},
            {"stage": "observed", "count": observed},
            {"stage": "oracle_evaluated", "count": oracle_evaluated},
            {"stage": "oracle_violation", "count": oracle_violation},
            {"stage": "deliverable", "count": deliverable},
        ],
        "upstream_receipts": {
            "scan_overlay_status": _text(overlay.get("status")),
            "scan_contract_added_count": _safe_int(overlay.get("contract_added_count")),
            "scan_coverage_gap_count": _safe_int(overlay.get("coverage_gap_count")),
            "source_binding_status": _text(source_binding.get("status")),
            "source_binding_gap_count": _safe_int(source_binding.get("coverage_gap_count")),
            "obligation_binding_status": _text(obligation_binding.get("status")),
            "misclassified_obligations_removed": _safe_int(
                obligation_binding.get("misclassified_obligation_count_removed")
            ),
            "complete_family_vector": obligation_binding.get("complete_family_vector") is True,
        },
        "cleanup_outcome_invariant": {
            "cleanup_equivalence_required_for_observed_outcome": True,
            "cleanup_equivalence_required_for_oracle": True,
            "cleanup_equivalence_required_for_delivery": True,
            "invalid_oracle_without_cleanup_count": invalid_cleanup_oracle_count,
            "invalid_deliverable_without_cleanup_count": (
                invalid_cleanup_deliverable_count
            ),
            "invalid_outcomes_counted": False,
        },
        "losses": {
            "source_binding_reason_counts": dict(sorted(binding_reason_counts.items())),
            "obligation_skip_reason_counts": dict(sorted(skipped_reason_counts.items())),
            "terminal_reason_counts": dict(sorted(terminal_reasons.items())),
            "observer_reason_counts": dict(sorted(observer_reason_counts.items())),
            "oracle_status_counts": dict(sorted(oracle_status_counts.items())),
        },
        "outcomes": {
            "property_held_count": oracle_property_held,
            "violation_count": oracle_violation,
            "deliverable_count": deliverable,
        },
        "professional_coverage": professional_coverage,
        "external_quality_metrics": {
            "status": "NOT_MEASURED",
            "recall": None,
            "precision": None,
            "f1": None,
            "required_evidence": "external_hidden_ground_truth_evaluator",
        },
        "provider_findings_consumed": False,
    }


def attach_formal_ui_loss_funnel(
    result: dict[str, Any],
    discovery_funnel: dict[str, Any],
) -> dict[str, Any]:
    projected = dict(discovery_funnel)
    surfaces = dict(_dict(projected.get("surface_funnels")))
    surfaces["formal_ui"] = build_formal_ui_loss_funnel(result)
    projected["surface_funnels"] = surfaces
    return projected


__all__ = [
    "attach_formal_ui_loss_funnel",
    "build_formal_ui_loss_funnel",
]
