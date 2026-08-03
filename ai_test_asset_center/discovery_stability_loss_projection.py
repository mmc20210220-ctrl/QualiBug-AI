"""Project formal short-window reliability losses from mainline receipts."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .formal_stability_surface import OBSERVER_ID, RISK_FAMILY
from .scan_post_hooks import register_scan_post_hook

HOOK_NAME = "formal_stability_loss"


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


def _stage(attempt: dict[str, Any], name: str) -> str:
    for row in _list(attempt.get("stages")):
        if isinstance(row, dict) and _text(row.get("stage")) == name:
            return _text(row.get("status")).upper()
    return ""


def build_formal_stability_loss_funnel(result: dict[str, Any]) -> dict[str, Any]:
    ir = _dict(result.get("behavior_ir"))
    pack = _dict(result.get("test_obligations"))
    overlay = _dict(ir.get("scan_stability_contract_overlay_receipt"))
    binding = _dict(ir.get("source_stability_contract_binding_receipt"))
    obligation_binding = _dict(pack.get("source_stability_obligation_receipt"))
    obligations = [
        dict(row) for row in _list(pack.get("obligations"))
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]
    attempts = [
        dict(row)
        for row in _list(_dict(result.get("obligation_attempt_ledger")).get("attempts"))
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]
    executions = {
        _text(row.get("obligation_id") or key): dict(row)
        for key, row in _dict(_dict(result.get("experiment_execution")).get("results")).items()
        if isinstance(row, dict)
    }
    compiled = sum(1 for row in attempts if _stage(row, "compile") == "COMPILED")
    executed = sum(1 for row in attempts if _stage(row, "execution") in {"EXECUTED", "DELIVERABLE"})
    observed = oracle_evaluated = violation = held = 0
    failed_samples = retried_samples = 0
    observer_reasons: Counter[str] = Counter()
    oracle_statuses: Counter[str] = Counter()
    for attempt in attempts:
        execution = _dict(executions.get(_text(attempt.get("obligation_id"))))
        receipts = [
            row for row in _list(execution.get("observer_receipts"))
            if isinstance(row, dict) and _text(row.get("observer_id")) == OBSERVER_ID
        ]
        if any(_text(row.get("status")).upper() == "OBSERVED" for row in receipts):
            observed += 1
        for receipt in receipts:
            reason = _text(receipt.get("reason_code"))
            if _text(receipt.get("status")).upper() != "OBSERVED" and reason:
                observer_reasons[reason] += 1
            evidence = _dict(_dict(receipt.get("evidence")).get("source_http_read_stability"))
            failed_samples += _safe_int(evidence.get("failed_sample_count"))
            retried_samples += _safe_int(evidence.get("retried_sample_count"))
        oracle = _dict(execution.get("oracle_verdict"))
        status = _text(oracle.get("status")).upper()
        if status:
            oracle_evaluated += 1
            oracle_statuses[status] += 1
        if status == "VIOLATION":
            violation += 1
        elif status == "PROPERTY_HELD":
            held += 1
    deliverable = sum(1 for row in attempts if _text(row.get("terminal_status")).upper() == "DELIVERABLE")
    terminal_reasons = Counter(
        _text(row.get("reason_code"))
        for row in attempts
        if _text(row.get("terminal_status")).upper() != "DELIVERABLE" and _text(row.get("reason_code"))
    )
    source_count = max(_safe_int(overlay.get("scan_contract_count")), _safe_int(binding.get("contract_count")))
    return {
        "schema_version": "qualibug.formal-stability-loss-funnel.v1",
        "risk_family": RISK_FAMILY,
        "observer_id": OBSERVER_ID,
        "measurement_scope": "source_declared_short_window_sequential_read_reliability",
        "stages": [
            {"stage": "source_contract", "count": source_count},
            {"stage": "ir_bound", "count": _safe_int(binding.get("bound_invariant_count"))},
            {"stage": "obligation_generated", "count": len(obligations)},
            {"stage": "selected", "count": len(attempts)},
            {"stage": "compiled", "count": compiled},
            {"stage": "executed", "count": executed},
            {"stage": "observed", "count": observed},
            {"stage": "oracle_evaluated", "count": oracle_evaluated},
            {"stage": "oracle_violation", "count": violation},
            {"stage": "deliverable", "count": deliverable},
        ],
        "upstream_receipts": {
            "scan_overlay_status": _text(overlay.get("status")),
            "scan_contract_added_count": _safe_int(overlay.get("contract_added_count")),
            "scan_coverage_gap_count": _safe_int(overlay.get("coverage_gap_count")),
            "source_binding_status": _text(binding.get("status")),
            "source_binding_gap_count": _safe_int(binding.get("coverage_gap_count")),
            "obligation_binding_status": _text(obligation_binding.get("status")),
            "complete_family_vector": obligation_binding.get("complete_family_vector") is True,
        },
        "losses": {
            "terminal_reason_counts": dict(sorted(terminal_reasons.items())),
            "observer_reason_counts": dict(sorted(observer_reasons.items())),
            "oracle_status_counts": dict(sorted(oracle_statuses.items())),
            "failed_sample_count": failed_samples,
            "retried_sample_count": retried_samples,
        },
        "outcomes": {
            "property_held_count": held,
            "violation_count": violation,
            "deliverable_count": deliverable,
        },
        "capability_boundary": {
            "methods": ["GET", "HEAD"],
            "sample_count_range": [5, 20],
            "short_window_sequential_only": True,
            "load_supported": False,
            "concurrency_supported": False,
            "long_duration_soak_supported": False,
            "recovery_or_failover_supported": False,
            "raw_response_payloads_included": False,
        },
        "external_quality_metrics": {
            "status": "NOT_MEASURED",
            "recall": None,
            "precision": None,
            "f1": None,
            "required_evidence": "external_hidden_ground_truth_evaluator",
        },
    }


def attach_formal_stability_loss_funnel(result: dict[str, Any], discovery_funnel: dict[str, Any]) -> dict[str, Any]:
    projected = dict(discovery_funnel)
    surfaces = dict(_dict(projected.get("surface_funnels")))
    surfaces["formal_stability"] = build_formal_stability_loss_funnel(result)
    projected["surface_funnels"] = surfaces
    return projected


__all__ = ["attach_formal_stability_loss_funnel", "build_formal_stability_loss_funnel"]


def attach_formal_stability_loss_hook(
    scan_result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Project short-window stability losses onto the scan's discovery funnel."""
    if not isinstance(scan_result, dict):
        return scan_result
    funnel = scan_result.get("discovery_funnel")
    if not isinstance(funnel, dict):
        return scan_result
    scan_result["discovery_funnel"] = attach_formal_stability_loss_funnel(scan_result, funnel)
    return scan_result


def install_formal_stability_loss() -> None:
    register_scan_post_hook(HOOK_NAME, attach_formal_stability_loss_hook)
