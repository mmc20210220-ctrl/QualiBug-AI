"""Receipt-backed discovery loss funnel without invented quality metrics.

This module measures only transitions that the formal discovery result proves:
obligation generation, selection, compile, execution, typed observation, oracle
evaluation and delivery. Recall/precision/F1 remain NOT_MEASURED unless an
external hidden ground-truth evaluator supplies TP/FP/FN.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

SCHEMA_VERSION = "qualibug.discovery-loss-funnel.v1"


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


def _fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _execution_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _dict(_dict(result).get("experiment_execution")).get("results")
    if isinstance(raw, dict):
        return [
            dict(row)
            for _, row in sorted(raw.items(), key=lambda item: str(item[0]))
            if isinstance(row, dict)
        ]
    return [dict(row) for row in _list(raw) if isinstance(row, dict)]


def _attempt_stage_status(attempt: dict[str, Any], stage: str) -> str:
    for row in _list(attempt.get("stages")):
        if isinstance(row, dict) and _text(row.get("stage")) == stage:
            return _text(row.get("status")).upper()
    return ""


def _attempt_is_selected(attempt: dict[str, Any]) -> bool:
    selection_status = _text(attempt.get("selection_status")).upper()
    return not selection_status or selection_status == "SELECTED"


def _stage(
    name: str,
    count: int,
    *,
    unit: str = "obligation",
    prior_count: int | None = None,
    evidence_source: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "stage": name,
        "count": max(0, int(count)),
        "unit": unit,
        "evidence_source": evidence_source,
    }
    if prior_count is not None and unit == "obligation":
        prior = max(0, int(prior_count))
        row["loss_from_prior"] = max(0, prior - row["count"])
        row["conversion_from_prior"] = (
            round(row["count"] / prior, 6) if prior else None
        )
    return row


def build_discovery_loss_funnel(result: dict[str, Any]) -> dict[str, Any]:
    """Build one honest funnel from formal result receipts."""

    if not isinstance(result, dict):
        raise TypeError("discovery_result_not_object")

    ledger = _dict(result.get("obligation_attempt_ledger"))
    attempts = [
        dict(row)
        for row in _list(ledger.get("attempts"))
        if isinstance(row, dict)
    ]
    execution_rows = _execution_rows(result)
    execution_by_obligation = {
        _text(row.get("obligation_id")): row
        for row in execution_rows
        if _text(row.get("obligation_id"))
    }

    generated = len([
        row
        for row in _list(_dict(result.get("test_obligations")).get("obligations"))
        if isinstance(row, dict)
    ])
    selected_raw = ledger.get("selected_count")
    selected = _safe_int(
        selected_raw
        if selected_raw is not None
        else _dict(result.get("experiment_execution")).get("selected_count")
    )
    selected_attempts = [attempt for attempt in attempts if _attempt_is_selected(attempt)]
    compiled = sum(
        1 for attempt in selected_attempts
        if _attempt_stage_status(attempt, "compile") == "COMPILED"
    )
    executed = sum(
        1 for attempt in selected_attempts
        if _attempt_stage_status(attempt, "execution") in {
            "EXECUTED",
            "DELIVERABLE",
        }
    )
    observed = 0
    oracle_evaluated = 0
    observer_status_counts: Counter[str] = Counter()
    observer_reason_counts: Counter[str] = Counter()
    for attempt in selected_attempts:
        obligation_id = _text(attempt.get("obligation_id"))
        execution = _dict(execution_by_obligation.get(obligation_id))
        observer_receipts = [
            row
            for row in _list(execution.get("observer_receipts"))
            if isinstance(row, dict)
        ]
        statuses = {
            _text(row.get("status")).upper()
            for row in observer_receipts
            if _text(row.get("status"))
        }
        observer_status_counts.update(
            _text(row.get("status")).upper()
            for row in observer_receipts
            if _text(row.get("status"))
        )
        observer_reason_counts.update(
            _text(row.get("reason_code"))
            for row in observer_receipts
            if _text(row.get("status")).upper() != "OBSERVED"
            and _text(row.get("reason_code"))
        )
        if "OBSERVED" in statuses:
            observed += 1
        oracle = _dict(execution.get("oracle_verdict"))
        if _text(oracle.get("status") or oracle.get("verdict")):
            oracle_evaluated += 1

    deliverable = sum(
        1 for attempt in selected_attempts
        if _text(attempt.get("terminal_status")).upper() == "DELIVERABLE"
    )
    canonical_ids = [
        _text(value)
        for value in _list(
            _dict(result.get("formal_count_projection")).get(
                "canonical_defect_ids"
            )
        )
        if _text(value)
    ]
    canonical_count = len(set(canonical_ids))
    if not canonical_ids:
        canonical_count = len({
            _text(row.get("canonical_defect_id") or row.get("finding_id") or row.get("id"))
            for row in _list(result.get("findings"))
            if isinstance(row, dict)
            and _text(row.get("canonical_defect_id") or row.get("finding_id") or row.get("id"))
        })

    stages = [
        _stage(
            "generated",
            generated,
            evidence_source="test_obligations.obligations",
        ),
        _stage(
            "selected",
            selected,
            prior_count=generated,
            evidence_source="obligation_attempt_ledger.selected_count",
        ),
        _stage(
            "compiled",
            compiled,
            prior_count=selected,
            evidence_source="obligation_attempt_ledger.attempts[].stages.compile",
        ),
        _stage(
            "executed",
            executed,
            prior_count=compiled,
            evidence_source="obligation_attempt_ledger.attempts[].stages.execution",
        ),
        _stage(
            "observed",
            observed,
            prior_count=executed,
            evidence_source="experiment_execution.results[].observer_receipts",
        ),
        _stage(
            "oracle_evaluated",
            oracle_evaluated,
            prior_count=observed,
            evidence_source="experiment_execution.results[].oracle_verdict",
        ),
        _stage(
            "deliverable",
            deliverable,
            prior_count=oracle_evaluated,
            evidence_source="obligation_attempt_ledger.attempts[].terminal_status",
        ),
        _stage(
            "canonical_defect",
            canonical_count,
            unit="canonical_defect",
            evidence_source="formal_count_projection.canonical_defect_ids",
        ),
    ]

    terminal_reason_counts = Counter(
        _text(attempt.get("reason_code"))
        for attempt in attempts
        if _text(attempt.get("terminal_status")).upper() != "DELIVERABLE"
        and _text(attempt.get("reason_code"))
    )
    terminal_stage_counts = Counter(
        _text(attempt.get("terminal_stage")) or "unknown"
        for attempt in attempts
        if _text(attempt.get("terminal_status")).upper() != "DELIVERABLE"
    )
    risk_family_block_counts = Counter(
        _text(attempt.get("risk_family")) or "unknown"
        for attempt in attempts
        if _text(attempt.get("terminal_status")).upper() != "DELIVERABLE"
    )

    behavior_ir = _dict(result.get("behavior_ir"))
    semantic_receipt = _dict(
        behavior_ir.get("semantic_operation_binding_receipt")
    )
    effect_observer_receipt = _dict(
        behavior_ir.get("effect_observer_binding_receipt")
    )
    semantic_link_receipt = _dict(
        result.get("agent_semantic_link_receipt")
    )
    execution_status = _text(
        _dict(_dict(result.get("phases")).get("execution")).get("status")
    ).lower()
    ledger_complete = bool(ledger.get("complete"))
    if execution_status == "plan_only":
        measurement_status = "PLAN_ONLY"
    elif not attempts and selected == 0:
        measurement_status = "NO_SELECTED_OBLIGATIONS"
    elif ledger_complete and len(attempts) == _safe_int(
        ledger.get("accounted_count")
        if ledger.get("accounted_count") is not None
        else len(attempts)
    ):
        measurement_status = "MEASURED"
    else:
        measurement_status = "PARTIAL"

    funnel: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "measurement_status": measurement_status,
        "measurement_scope": "formal_runtime_conversion_only",
        "run_id": _text(ledger.get("run_id")),
        "campaign_id": _text(ledger.get("campaign_id")),
        "ledger_complete": ledger_complete,
        "accounted_count": _safe_int(
            ledger.get("accounted_count")
            if ledger.get("accounted_count") is not None
            else len(attempts)
        ),
        "selected_terminal_count": len(selected_attempts),
        "selection_status_counts": dict(
            sorted(
                Counter(
                    _text(attempt.get("selection_status")).upper() or "SELECTED"
                    for attempt in attempts
                ).items()
            )
        ),
        "stages": stages,
        "upstream_readiness": {
            "semantic_link_status": _text(semantic_link_receipt.get("status")),
            "semantic_proposal_count": _safe_int(
                semantic_link_receipt.get("proposal_count")
            ),
            "accepted_semantic_relationship_count": _safe_int(
                semantic_link_receipt.get("accepted_relationship_count")
            ),
            "semantic_operation_binding_status": _text(
                semantic_receipt.get("status")
            ),
            "accepted_operation_binding_count": _safe_int(
                semantic_receipt.get("accepted_binding_count")
            ),
            "bound_invariant_count": _safe_int(
                semantic_receipt.get("bound_invariant_count")
            ),
            "effect_observer_binding_status": _text(
                effect_observer_receipt.get("status")
            ),
            "effect_observer_relation_count": _safe_int(
                effect_observer_receipt.get("added_relation_count")
            ),
        },
        "losses": {
            "terminal_reason_counts": dict(sorted(terminal_reason_counts.items())),
            "terminal_stage_counts": dict(sorted(terminal_stage_counts.items())),
            "blocked_risk_family_counts": dict(sorted(risk_family_block_counts.items())),
            "observer_status_counts": dict(sorted(observer_status_counts.items())),
            "observer_reason_counts": dict(sorted(observer_reason_counts.items())),
            "top_terminal_blockers": [
                {"reason_code": reason, "count": count}
                for reason, count in terminal_reason_counts.most_common(10)
            ],
        },
        "evidence_projection": {
            "evidence_graph_count": len([
                row for row in _list(result.get("evidence_graphs"))
                if isinstance(row, dict)
            ]),
            "execution_trace_summary_count": len([
                row for row in _list(result.get("execution_trace_summaries"))
                if isinstance(row, dict)
            ]),
        },
        "external_quality_metrics": {
            "status": "NOT_MEASURED",
            "recall": None,
            "precision": None,
            "f1": None,
            "true_positive": None,
            "false_positive": None,
            "false_negative": None,
            "required_evidence": "external_hidden_ground_truth_evaluator",
        },
        "interpretation_contract": {
            "selection_loss_is_not_automatically_a_bug_miss": True,
            "canonical_defect_count_uses_a_different_unit": True,
            "no_ground_truth_quality_claims": True,
        },
    }
    funnel["funnel_fingerprint"] = _fingerprint(funnel)
    return funnel


__all__ = ["build_discovery_loss_funnel"]
