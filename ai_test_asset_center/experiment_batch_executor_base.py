"""Outcome-fanout facade over the stable batch executor.

The private mechanics still executes each selected experiment once and processes the first
compatibility finding. This authority then materializes every additional violated outcome as
an independent delivery occurrence with its own Oracle, execution, reproduction, Gate, and
finding receipt chain.
"""
from __future__ import annotations

from copy import deepcopy
import logging
from pathlib import Path
from typing import Any

from . import _experiment_batch_executor_single_finding_mechanics as _core
from ._experiment_batch_executor_single_finding_mechanics import *  # noqa: F401,F403
from .experiment_batch_concurrent_scheduler import (
    execute_selected_experiments_concurrent,
)
from .contract_oracles import (
    project_contract_oracle_for_outcome,
    validate_contract_oracle_receipt,
)
from .customer_delivery_gate_v2 import (
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
)
from .small_scale_validation_gate import VALIDATION_GATE_SCHEMA


logger = logging.getLogger(__name__)

_original_execute_selected_experiments = _core.execute_selected_experiments


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stamp_occurrence_identity(
    finding: dict[str, Any],
    *,
    outcome: dict[str, Any],
    run_contract: dict[str, Any],
    occurrence_index: int,
) -> dict[str, Any]:
    row = deepcopy(_dict(finding))
    outcome_ref = _text(row.get("outcome_ref"))
    finding_id = _text(row.get("finding_id") or row.get("id")) or _core._stable_id(
        "finding",
        _text(outcome.get("evidence_id")),
        outcome_ref or occurrence_index,
    )
    row.update(
        {
            "id": finding_id,
            "finding_id": finding_id,
            "candidate_id": _text(outcome.get("candidate_id")),
            "behavior_slice_id": _text(outcome.get("slice_id")),
            "slice_id": _text(outcome.get("slice_id")),
            "selected_obligation_id": _text(outcome.get("selected_obligation_id")),
            "obligation_id": _text(outcome.get("obligation_id")),
            "experiment_id": _text(outcome.get("experiment_id")),
            "execution_id": _text(outcome.get("execution_id")),
            "evidence_id": _text(outcome.get("evidence_id")),
            "campaign_id": _text(outcome.get("campaign_id")),
            "mainline_run": {
                "contract_fingerprint": _text(run_contract.get("contract_fingerprint"))
            },
            "outcome_occurrence_index": occurrence_index,
        }
    )
    evidence = dict(_dict(row.get("evidence")))
    evidence.update(
        {
            "evidence_id": _text(outcome.get("evidence_id")),
            "execution_id": _text(outcome.get("execution_id")),
            "outcome_ref": outcome_ref,
        }
    )
    row["evidence"] = evidence
    return row


def _occurrence_oracles(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        validate_contract_oracle_receipt(_dict(row))
        for row in _list(outcome.get("outcome_oracle_receipts"))
        if isinstance(row, dict)
    ]
    if rows:
        return rows
    aggregate = _dict(outcome.get("aggregate_oracle_verdict"))
    if not aggregate:
        oracle = _dict(outcome.get("oracle_verdict"))
        return [validate_contract_oracle_receipt(oracle)] if oracle else []
    parent = validate_contract_oracle_receipt(aggregate)
    return [
        project_contract_oracle_for_outcome(parent, ref)
        for ref in _list(parent.get("violation_outcome_refs"))
        if _text(ref)
    ]


def _build_occurrence(
    *,
    finding: dict[str, Any],
    oracle: dict[str, Any],
    parent_oracle: dict[str, Any],
    outcome: dict[str, Any],
    experiment: dict[str, Any],
    run_contract: dict[str, Any],
    occurrence_index: int,
) -> dict[str, Any]:
    stamped = _stamp_occurrence_identity(
        finding,
        outcome=outcome,
        run_contract=run_contract,
        occurrence_index=occurrence_index,
    )
    primary_ref = _text(oracle.get("primary_violation_outcome_ref"))
    if _text(stamped.get("outcome_ref")) != primary_ref:
        raise ValueError("fanout_finding_oracle_outcome_ref_mismatch")

    existing_finding = _dict(outcome.get("finding"))
    existing_gate = _dict(outcome.get("delivery_gate_receipt"))
    existing_oracle = _dict(outcome.get("oracle_verdict"))
    if (
        existing_finding
        and existing_gate
        and _text(existing_finding.get("outcome_ref")) == primary_ref
        and _text(existing_oracle.get("receipt_id")) == _text(oracle.get("receipt_id"))
    ):
        primary_finding = deepcopy(existing_finding)
        return {
            "finding_id": _text(primary_finding.get("finding_id")),
            "outcome_ref": primary_ref,
            "finding": primary_finding,
            "delivery_execution_receipt": dict(
                _dict(outcome.get("delivery_execution_receipt"))
            ),
            "contract_evidence_receipts": [
                dict(row)
                for row in _list(outcome.get("contract_evidence_receipts"))
                if isinstance(row, dict)
            ],
            "observer_receipts": [
                dict(row)
                for row in _list(outcome.get("observer_receipts"))
                if isinstance(row, dict)
            ],
            "oracle_receipt": dict(oracle),
            "parent_oracle_receipt": dict(parent_oracle),
            "reproduction_receipt": dict(_dict(outcome.get("reproduction_receipt"))),
            "gate_receipt": dict(existing_gate),
        }

    primary_execution = _dict(outcome.get("delivery_execution_receipt"))
    observation_ids = [
        _text(value)
        for value in _list(primary_execution.get("observation_receipt_ids"))
        if _text(value)
    ]
    delivery_execution = build_delivery_execution_receipt(
        mainline_run=run_contract,
        candidate_id=_text(outcome.get("candidate_id")),
        slice_id=_text(outcome.get("slice_id")),
        obligation_id=_text(outcome.get("obligation_id")),
        experiment_id=_text(outcome.get("experiment_id")),
        execution_id=_text(outcome.get("execution_id")),
        evidence_id=_text(outcome.get("evidence_id")),
        operational_receipt=_dict(outcome.get("operational_receipt")),
        observation_receipt_ids=observation_ids,
        oracle_receipt_id=_text(oracle.get("receipt_id")),
        elapsed_ms=outcome.get("elapsed_ms"),
        cost_coverage_status=_text(
            primary_execution.get("cost_coverage_status") or "UNKNOWN"
        ),
    )
    reproduction = build_reproduction_receipt(
        execution_receipt=delivery_execution,
        steps=[
            dict(row) for row in _list(outcome.get("steps")) if isinstance(row, dict)
        ],
        oracle_receipt=oracle,
        source_refs=[
            dict(row)
            for row in _list(experiment.get("source_refs"))
            if isinstance(row, dict)
        ],
    )
    contracts = [
        dict(row)
        for row in _list(outcome.get("contract_evidence_receipts"))
        if isinstance(row, dict)
    ]
    observers = [
        dict(row)
        for row in _list(outcome.get("observer_receipts"))
        if isinstance(row, dict)
    ]
    gate = build_customer_delivery_gate_receipt_v2(
        finding=stamped,
        execution_receipt=delivery_execution,
        contract_evidence_receipts=contracts,
        observer_receipts=observers,
        oracle_receipt=oracle,
        reproduction_receipt=reproduction,
    )
    if _text(gate.get("status")) == "DELIVERABLE":
        stamped = _core.finalize_finding_evidence_after_delivery_gate(
            stamped,
            gate_receipt=gate,
            reproduction_receipt=reproduction,
        )
        gate = build_customer_delivery_gate_receipt_v2(
            finding=stamped,
            execution_receipt=delivery_execution,
            contract_evidence_receipts=contracts,
            observer_receipts=observers,
            oracle_receipt=oracle,
            reproduction_receipt=reproduction,
        )
    stamped = _core.stamp_finding_delivery_gate_refs(stamped, gate_receipt=gate)
    return {
        "finding_id": _text(stamped.get("finding_id")),
        "outcome_ref": primary_ref,
        "finding": stamped,
        "delivery_execution_receipt": delivery_execution,
        "contract_evidence_receipts": contracts,
        "observer_receipts": observers,
        "oracle_receipt": dict(oracle),
        "parent_oracle_receipt": dict(parent_oracle),
        "reproduction_receipt": reproduction,
        "gate_receipt": gate,
    }


def _select_primary_occurrence(
    occurrences: list[dict[str, Any]],
) -> dict[str, Any]:
    deliverable = [
        row
        for row in occurrences
        if _text(_dict(row.get("gate_receipt")).get("status")) == "DELIVERABLE"
    ]
    pool = deliverable or occurrences
    return sorted(
        pool,
        key=lambda row: (_text(row.get("outcome_ref")), _text(row.get("finding_id"))),
    )[0]


def _apply_fanout(
    batch: dict[str, Any],
    *,
    selected: list[Any],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    mainline_run: dict[str, Any],
    campaign_id: str,
) -> dict[str, Any]:
    output = dict(batch)
    execution_results = dict(_dict(output.get("execution_results")))
    gate_results = dict(_dict(output.get("gate_results")))
    all_findings: list[dict[str, Any]] = []
    total_occurrences = 0

    for raw_outcome in _list(output.get("results")):
        if not isinstance(raw_outcome, dict):
            continue
        outcome = raw_outcome
        finding_templates = [
            dict(row)
            for row in _list(outcome.get("findings"))
            if isinstance(row, dict)
        ]
        if not finding_templates and isinstance(outcome.get("finding"), dict):
            finding_templates = [dict(outcome["finding"])]
        if not finding_templates:
            continue
        canonical_collection = bool(
            _dict(outcome.get("aggregate_oracle_verdict"))
            or _list(outcome.get("outcome_oracle_receipts"))
        )
        if not canonical_collection:
            existing = _dict(outcome.get("finding"))
            if existing:
                all_findings.append(dict(existing))
            continue
        oracles = _occurrence_oracles(outcome)
        if not oracles:
            raise ValueError("fanout_outcome_oracle_receipts_missing")
        by_ref = {_text(row.get("outcome_ref")): row for row in finding_templates}
        if len(by_ref) != len(finding_templates):
            raise ValueError("fanout_finding_outcome_ref_duplicate")
        parent_oracle = _dict(outcome.get("aggregate_oracle_verdict")) or _dict(
            outcome.get("oracle_verdict")
        )
        selected_oid = _text(outcome.get("selected_obligation_id"))
        experiment = _dict(experiments_by_obligation.get(selected_oid))
        occurrences: list[dict[str, Any]] = []
        for index, oracle in enumerate(
            sorted(
                oracles,
                key=lambda row: _text(row.get("primary_violation_outcome_ref")),
            ),
            start=1,
        ):
            ref = _text(oracle.get("primary_violation_outcome_ref"))
            template = by_ref.get(ref)
            if template is None:
                raise ValueError(f"fanout_finding_missing:{ref}")
            occurrences.append(
                _build_occurrence(
                    finding=template,
                    oracle=oracle,
                    parent_oracle=parent_oracle,
                    outcome=outcome,
                    experiment=experiment,
                    run_contract=mainline_run,
                    occurrence_index=index,
                )
            )
        if not occurrences:
            continue
        total_occurrences += len(occurrences)
        primary = _select_primary_occurrence(occurrences)
        primary_finding = dict(_dict(primary.get("finding")))
        outcome["delivery_occurrences"] = occurrences
        outcome["findings"] = [dict(_dict(row.get("finding"))) for row in occurrences]
        outcome["finding"] = primary_finding
        outcome["oracle_verdict"] = dict(_dict(primary.get("oracle_receipt")))
        outcome["delivery_execution_receipt"] = dict(
            _dict(primary.get("delivery_execution_receipt"))
        )
        outcome["reproduction_receipt"] = dict(
            _dict(primary.get("reproduction_receipt"))
        )
        outcome["delivery_gate_receipt"] = dict(_dict(primary.get("gate_receipt")))

        execution_result = dict(_dict(execution_results.get(selected_oid)))
        execution_result.update(
            {
                "finding": primary_finding,
                "delivery_execution_receipt": dict(
                    _dict(primary.get("delivery_execution_receipt"))
                ),
                "oracle_receipt": dict(_dict(primary.get("oracle_receipt"))),
                "oracle_receipt_id": _text(
                    _dict(primary.get("oracle_receipt")).get("receipt_id")
                ),
                "reproduction_receipt": dict(
                    _dict(primary.get("reproduction_receipt"))
                ),
                "delivery_occurrences": occurrences,
                "delivery_occurrence_count": len(occurrences),
                "delivery_occurrence_finding_ids": sorted(
                    _text(row.get("finding_id")) for row in occurrences
                ),
                "aggregate_oracle_receipt": dict(parent_oracle),
            }
        )
        primary_execution = _dict(primary.get("delivery_execution_receipt"))
        execution_result["receipt_id"] = _text(primary_execution.get("receipt_id"))
        execution_result["output_fingerprint"] = _text(
            primary_execution.get("receipt_fingerprint")
        )
        execution_results[selected_oid] = execution_result
        gate_results[selected_oid] = dict(_dict(primary.get("gate_receipt")))
        all_findings.extend(outcome["findings"])

    if all_findings:
        output["findings"] = sorted(
            all_findings,
            key=lambda row: (
                _text(row.get("execution_id")),
                _text(row.get("outcome_ref")),
            ),
        )
    output["execution_results"] = execution_results
    output["gate_results"] = gate_results
    output["delivery_occurrence_count"] = total_occurrences or len(
        _list(output.get("findings"))
    )

    try:
        from .execution_coverage_funnel import build_execution_coverage_funnel

        selected_rows = [_dict(row) for row in selected]
        selected_ids = [_text(row.get("obligation_id")) for row in selected_rows]
        output["execution_coverage_funnel"] = build_execution_coverage_funnel(
            obligations=selected_rows,
            experiments=[
                experiments_by_obligation.get(obligation_id, {})
                for obligation_id in selected_ids
            ],
            execution_results=list(execution_results.values()),
            findings=_list(output.get("findings")),
            campaign_id=campaign_id,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced in campaign receipt
        receipt = dict(_dict(output.get("campaign_validation_receipt")))
        receipt["campaign_validation_status"] = "FAILED"
        reasons = [
            _text(value) for value in _list(receipt.get("reasons")) if _text(value)
        ]
        reasons.append(
            f"HARNESS_COVERAGE_FUNNEL_FAILED:{type(exc).__name__}:{exc}"[:180]
        )
        receipt["reasons"] = sorted(set(reasons))
        receipt["customer_deliverable"] = False
        output["campaign_validation_receipt"] = receipt

    try:
        from .small_scale_validation_gate import check_validation_gate

        output["validation_gate"] = check_validation_gate(
            output,
            campaign_id=campaign_id,
            run_id=_text(mainline_run.get("run_id")),
            phase=_text(output.get("validation_phase")),
        )
    except Exception as exc:
        # Validation is an execution gate, not optional decoration.  Preserve
        # the original exception in logs while exposing a redacted, stable
        # reason code to downstream campaign and funnel projections.
        logger.exception(
            "validation gate evaluation failed for campaign %s",
            campaign_id,
        )
        validation_gate = {
            "schema_version": VALIDATION_GATE_SCHEMA,
            "status": "FAILED",
            "campaign_id": campaign_id,
            "run_id": _text(mainline_run.get("run_id")),
            "phase": _text(output.get("validation_phase")),
            "reason_code": "VALIDATION_GATE_EXCEPTION",
            "reason": "validation_gate_evaluation_failed",
            "error_class": type(exc).__name__,
            "customer_deliverable": False,
        }
        output["validation_gate"] = validation_gate
        receipt = dict(_dict(output.get("campaign_validation_receipt")))
        receipt["campaign_validation_status"] = "FAILED"
        reasons = [
            _text(value) for value in _list(receipt.get("reasons")) if _text(value)
        ]
        reasons.append("VALIDATION_GATE_EXCEPTION")
        receipt["reasons"] = sorted(set(reasons))
        receipt["customer_deliverable"] = False
        output["campaign_validation_receipt"] = receipt
    return output


def execute_selected_experiments(
    selected: list[Any],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
    root: Any,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    mainline_run: dict[str, Any],
    campaign_id: str = "",
    experiment_budget: int = 100,
    validation_phase: str = "",
) -> dict[str, Any]:
    # Task 9: resource-domain isolated concurrency (default 8 workers). The
    # scheduler reuses the serial core per serial group, preserves the global
    # budget/prioritization semantics and aggregates receipts in the original
    # selected order. ``_original_execute_selected_experiments`` remains the
    # pure-serial path (tests / operator fallback).
    batch = execute_selected_experiments_concurrent(
        selected,
        experiments_by_obligation=experiments_by_obligation,
        behavior_ir=behavior_ir,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        mainline_run=mainline_run,
        campaign_id=campaign_id,
        experiment_budget=experiment_budget,
        validation_phase=validation_phase,
    )
    output = _apply_fanout(
        _dict(batch),
        selected=selected,
        experiments_by_obligation=experiments_by_obligation,
        behavior_ir=behavior_ir,
        mainline_run=mainline_run,
        campaign_id=campaign_id,
    )
    # ── Operator-cancel single consume point ──
    # All parallel groups observed the same pending marker read-only; this
    # entry consumes it exactly once so a stale request can never leak into a
    # later scan. Lease-directory removal remains the failure-safe cleanup.
    if int(_dict(output).get("operator_cancelled_count") or 0) > 0:
        try:
            from .scan_cancellation import consume_scan_cancel_request

            consumed = consume_scan_cancel_request(Path(root), project)
            receipt = dict(_dict(output).get("operator_cancelled_receipt") or {})
            if not receipt and consumed:
                receipt = {
                    "schema": "qualibug.scan-cancel-request.v1",
                    "requested_at_utc": _text(consumed.get("requested_at_utc")),
                    "requester": dict(consumed.get("requester") or {}),
                }
            if receipt:
                output["operator_cancelled_receipt"] = receipt
        except Exception as exc:  # pragma: no cover - observability path
            logger.warning(
                "scan_cancel_consume_failed error_type=%s error=%s",
                type(exc).__name__,
                str(exc)[:200],
            )
    return output


__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
