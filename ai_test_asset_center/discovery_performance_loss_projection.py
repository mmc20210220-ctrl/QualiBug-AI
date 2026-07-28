"""Project formal performance losses from existing mainline receipts.

The funnel covers only source-declared sequential GET/HEAD latency budgets. It
must not be read as load, throughput, concurrency or stability coverage.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .formal_performance_surface import OBSERVER_ID, RISK_FAMILY


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
    rows = _dict(_dict(result.get("experiment_execution")).get("results"))
    return {
        _text(row.get("obligation_id") or key): dict(row)
        for key, row in rows.items()
        if isinstance(row, dict) and _text(row.get("obligation_id") or key)
    }


def build_formal_performance_loss_funnel(
    result: dict[str, Any],
) -> dict[str, Any]:
    behavior_ir = _dict(result.get("behavior_ir"))
    obligation_pack = _dict(result.get("test_obligations"))
    overlay = _dict(behavior_ir.get("scan_performance_contract_overlay_receipt"))
    binding = _dict(behavior_ir.get("source_performance_contract_binding_receipt"))
    obligation_binding = _dict(
        obligation_pack.get("source_performance_obligation_receipt")
    )
    obligations = [
        dict(row)
        for row in _list(obligation_pack.get("obligations"))
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]
    attempts = [
        dict(row)
        for row in _list(
            _dict(result.get("obligation_attempt_ledger")).get("attempts")
        )
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]
    execution_by_obligation = _execution_rows(result)

    compiled = sum(
        1 for row in attempts if _stage_status(row, "compile") == "COMPILED"
    )
    executed = sum(
        1
        for row in attempts
        if _stage_status(row, "execution") in {"EXECUTED", "DELIVERABLE"}
    )
    observed = 0
    oracle_evaluated = 0
    oracle_violation = 0
    property_held = 0
    observer_reasons: Counter[str] = Counter()
    oracle_statuses: Counter[str] = Counter()
    retried_samples = 0
    functional_samples_suppressed = 0
    for attempt in attempts:
        execution = _dict(
            execution_by_obligation.get(_text(attempt.get("obligation_id")))
        )
        receipts = [
            row
            for row in _list(execution.get("observer_receipts"))
            if isinstance(row, dict) and _text(row.get("observer_id")) == OBSERVER_ID
        ]
        if any(_text(row.get("status")).upper() == "OBSERVED" for row in receipts):
            observed += 1
        for receipt in receipts:
            if _text(receipt.get("status")).upper() != "OBSERVED":
                reason = _text(receipt.get("reason_code"))
                if reason:
                    observer_reasons[reason] += 1
            evidence = _dict(
                _dict(receipt.get("evidence")).get("source_http_latency_series")
            )
            retried_samples += _safe_int(evidence.get("retried_sample_count"))
            functional_samples_suppressed += _safe_int(
                evidence.get("non_success_sample_count")
            )
        oracle = _dict(execution.get("oracle_verdict"))
        status = _text(oracle.get("status")).upper()
        if status:
            oracle_evaluated += 1
            oracle_statuses[status] += 1
        if status == "VIOLATION":
            oracle_violation += 1
        elif status == "PROPERTY_HELD":
            property_held += 1

    deliverable = sum(
        1
        for row in attempts
        if _text(row.get("terminal_status")).upper() == "DELIVERABLE"
    )
    terminal_reasons = Counter(
        _text(row.get("reason_code"))
        for row in attempts
        if _text(row.get("terminal_status")).upper() != "DELIVERABLE"
        and _text(row.get("reason_code"))
    )
    binding_reasons = {
        _text(key): _safe_int(value)
        for key, value in _dict(binding.get("reason_counts")).items()
        if _text(key)
    }
    skip_reasons = {
        _text(key): _safe_int(value)
        for key, value in _dict(
            obligation_binding.get("skipped_reason_counts")
        ).items()
        if _text(key)
    }
    source_count = max(
        _safe_int(overlay.get("scan_contract_count")),
        _safe_int(binding.get("contract_count")),
    )

    return {
        "schema_version": "qualibug.formal-performance-loss-funnel.v1",
        "risk_family": RISK_FAMILY,
        "observer_id": OBSERVER_ID,
        "measurement_scope": "source_declared_sequential_get_head_latency_only",
        "stages": [
            {"stage": "source_contract", "count": source_count},
            {"stage": "ir_bound", "count": _safe_int(binding.get("bound_invariant_count"))},
            {"stage": "obligation_generated", "count": len(obligations)},
            {"stage": "selected", "count": len(attempts)},
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
            "source_binding_status": _text(binding.get("status")),
            "source_binding_gap_count": _safe_int(binding.get("coverage_gap_count")),
            "obligation_binding_status": _text(obligation_binding.get("status")),
            "complete_family_vector": obligation_binding.get("complete_family_vector") is True,
        },
        "losses": {
            "source_binding_reason_counts": dict(sorted(binding_reasons.items())),
            "obligation_skip_reason_counts": dict(sorted(skip_reasons.items())),
            "terminal_reason_counts": dict(sorted(terminal_reasons.items())),
            "observer_reason_counts": dict(sorted(observer_reasons.items())),
            "oracle_status_counts": dict(sorted(oracle_statuses.items())),
            "retried_sample_count": retried_samples,
            "functional_response_sample_count_suppressed": functional_samples_suppressed,
        },
        "outcomes": {
            "property_held_count": property_held,
            "violation_count": oracle_violation,
            "deliverable_count": deliverable,
        },
        "capability_boundary": {
            "load_capacity_supported": False,
            "throughput_supported": False,
            "concurrent_users_supported": False,
            "long_duration_stability_supported": False,
            "methods": ["GET", "HEAD"],
            "sample_count_range": [3, 20],
            "percentile_method": "nearest_rank",
            "transport_retries_accepted": False,
            "functional_non_2xx_judged_as_performance": False,
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


def attach_formal_performance_loss_funnel(
    result: dict[str, Any],
    discovery_funnel: dict[str, Any],
) -> dict[str, Any]:
    projected = dict(discovery_funnel)
    surfaces = dict(_dict(projected.get("surface_funnels")))
    surfaces["formal_performance"] = build_formal_performance_loss_funnel(result)
    projected["surface_funnels"] = surfaces
    return projected


__all__ = [
    "attach_formal_performance_loss_funnel",
    "build_formal_performance_loss_funnel",
]
