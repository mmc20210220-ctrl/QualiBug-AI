"""Canonical observer/assertion surface for graph async transitions.

The runtime publishes exact-step event transition receipts and the
ProcessStepLedger remains the timeline authority. This module projects those
immutable receipts into the existing Observer/Assertion DSL so measured
message, callback, retry, idempotency, and broker-delivery violations become
Oracle violations rather than harness failures.
"""
from __future__ import annotations

from typing import Any

from .process_graph_event_transition import RECEIPT_SCHEMA_VERSION


OBSERVER_ID = "process_graph_async_transition"
SURFACE = "process_graph_async_transition"
ADAPTER = "http_api"
EVIDENCE_KEY = "process_graph_async_transition"
ASSERTION_KIND = "process_async_completion"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _declared_event_contracts(
    experiment: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    graph = _dict(_dict(experiment).get("execution_graph"))
    contracts: dict[str, dict[str, Any]] = {}
    for wait in _list(graph.get("wait_contracts")):
        row = _dict(wait)
        event = _dict(row.get("event_transition_contract"))
        target = _text(row.get("target_node_id"))
        if (
            _text(row.get("transition_kind")) == "event_delivery"
            and target
            and event
        ):
            contracts[target] = event
    return contracts


def _broker_projection(receipt: dict[str, Any]) -> dict[str, Any]:
    evidence = _dict(receipt.get("broker_evidence"))
    allowed = (
        "contract_fingerprint",
        "broker_model",
        "metadata_incomplete_count",
        "topic_mismatch_count",
        "consumer_group_mismatch_count",
        "partition_count",
        "observed_offset_count",
        "partition_offset_conflict_count",
        "checkpoint_conflict_count",
        "checkpoint_regression_count",
        "checkpoint_behind_observed_count",
        "dlq_delivery_count",
        "unexpected_dlq_delivery_count",
        "sequence_order_violation_count",
        "restart_replay_count",
        "restart_duplicate_effect_count",
        "topic_fingerprints",
        "consumer_group_fingerprints",
        "consumer_epoch_fingerprints",
        "checkpoint_state_fingerprint",
    )
    return {
        key: evidence[key]
        for key in allowed
        if key in evidence
    }


def observe_async_transitions(envelope: dict[str, Any]) -> dict[str, Any]:
    """Project exactly one runtime receipt per compile-frozen event target."""
    from .observer_contracts_base import _receipt

    experiment = _dict(envelope.get("experiment"))
    observations = _dict(envelope.get("observations"))
    contracts = _declared_event_contracts(experiment)
    if not contracts:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="PROCESS_GRAPH_EVENT_CONTRACTS_MISSING",
            evidence={"declared_transition_count": 0},
        )

    receipts_by_target: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(
        observations.get("process_graph_async_transition_receipts")
    ):
        receipt = _dict(raw)
        target = _text(receipt.get("target_node_id") or receipt.get("step_id"))
        if target:
            receipts_by_target.setdefault(target, []).append(receipt)

    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for target, contract in contracts.items():
        receipts = receipts_by_target.get(target, [])
        if len(receipts) != 1:
            issues.append(f"{target}:receipt_count={len(receipts)}")
            continue
        receipt = receipts[0]
        declared_broker = _dict(contract.get("broker_delivery_contract"))
        broker_scope_mismatch = bool(declared_broker) and (
            _text(receipt.get("broker_contract_fingerprint"))
            != _text(declared_broker.get("contract_fingerprint"))
        )
        if (
            _text(receipt.get("schema_version")) != RECEIPT_SCHEMA_VERSION
            or _text(receipt.get("contract_fingerprint"))
            != _text(contract.get("contract_fingerprint"))
            or _text(receipt.get("source_node_id"))
            != _text(contract.get("source_node_id"))
            or _text(receipt.get("target_node_id")) != target
            or broker_scope_mismatch
        ):
            issues.append(f"{target}:receipt_contract_scope_mismatch")
            continue
        semantic_reason_codes = [
            _text(value)
            for value in _list(receipt.get("semantic_reason_codes"))
            if _text(value)
        ]
        if not semantic_reason_codes and _text(receipt.get("reason_code")):
            semantic_reason_codes = [_text(receipt.get("reason_code"))]
        rows.append(
            {
                "step_id": target,
                "source_step_id": _text(receipt.get("source_node_id")),
                "target_step_id": target,
                "edge_id": _text(contract.get("edge_id")),
                "delivery_kind": _text(receipt.get("delivery_kind")),
                "delivery_semantics": _text(
                    receipt.get("delivery_semantics")
                ),
                "semantic_status": _text(
                    receipt.get("semantic_status")
                ).upper(),
                "reason_code": _text(receipt.get("reason_code")),
                "semantic_reason_codes": semantic_reason_codes,
                "coverage_complete": (
                    receipt.get("coverage_complete") is True
                ),
                "observation_window_completed": (
                    receipt.get("observation_window_completed") is True
                ),
                "attempt_count": int(receipt.get("attempt_count") or 0),
                "event_scope_mode": _text(
                    receipt.get("event_scope_mode")
                ),
                "idempotency_scope_authority": _text(
                    receipt.get("idempotency_scope_authority")
                ),
                "idempotency_binding_contract_fingerprint": _text(
                    receipt.get("idempotency_binding_contract_fingerprint")
                ),
                "source_request_contract_fingerprint": _text(
                    receipt.get("source_request_contract_fingerprint")
                ),
                "observed_correlated_row_count": int(
                    receipt.get("observed_correlated_row_count") or 0
                ),
                "observed_matching_row_count": int(
                    receipt.get("observed_matching_row_count") or 0
                ),
                "observed_unique_event_count": int(
                    receipt.get("observed_unique_event_count") or 0
                ),
                "distinct_delivery_overflow_count": int(
                    receipt.get("distinct_delivery_overflow_count") or 0
                ),
                "event_id_reuse_conflict_count": int(
                    receipt.get("event_id_reuse_conflict_count") or 0
                ),
                "event_identity_type_conflict_count": int(
                    receipt.get("event_identity_type_conflict_count") or 0
                ),
                "correlation_identity_mismatch_count": int(
                    receipt.get("correlation_identity_mismatch_count") or 0
                ),
                "idempotency_mismatch_count": int(
                    receipt.get("idempotency_mismatch_count") or 0
                ),
                "out_of_scope_idempotency_event_count": int(
                    receipt.get("out_of_scope_idempotency_event_count") or 0
                ),
                "missing_idempotency_event_count": int(
                    receipt.get("missing_idempotency_event_count") or 0
                ),
                "retry_limit_violation_count": int(
                    receipt.get("retry_limit_violation_count") or 0
                ),
                "broker_contract_fingerprint": _text(
                    receipt.get("broker_contract_fingerprint")
                ),
                "broker_semantic_status": _text(
                    receipt.get("broker_semantic_status")
                ).upper(),
                "broker_reason_codes": [
                    _text(value)
                    for value in _list(receipt.get("broker_reason_codes"))
                    if _text(value)
                ],
                "broker_evidence": _broker_projection(receipt),
                "receipt_id": _text(receipt.get("receipt_id")),
            }
        )

    complete = bool(rows) and not issues and len(rows) == len(contracts)
    if complete and any(
        row["semantic_status"] not in {"PASS", "VIOLATION"}
        or not row["coverage_complete"]
        or not row["observation_window_completed"]
        for row in rows
    ):
        complete = False
        issues.append("transition_observation_incomplete")

    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED" if complete else "INDETERMINATE",
        reason_code=(
            "" if complete else "PROCESS_GRAPH_EVENT_OBSERVATION_INCOMPLETE"
        ),
        evidence={
            EVIDENCE_KEY: {
                "surface": SURFACE,
                "declared_transition_count": len(contracts),
                "observed_transition_count": len(rows),
                "coverage_complete": complete,
                "issues": issues,
                "transitions": rows,
            }
        },
    )


def evaluate_process_async_completion(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Require all measured async transitions and the process flow to hold.

    A complete, contract-scoped async violation is already a proven target
    defect. Preserve it even when a later business node fails or never reaches
    transport; downstream incompleteness must not erase stronger prior evidence.
    """
    spec = _dict(envelope.get("spec"))
    observations = _dict(envelope.get("observations"))
    process = _dict(observations.get("process_step_timeline"))
    async_rows = _dict(observations.get(EVIDENCE_KEY))

    expected_steps = [
        _text(value)
        for value in _list(
            spec.get("expected_steps")
            or _dict(spec.get("property")).get("expected_steps")
        )
        if _text(value)
    ]
    expected_order = [
        _text(value)
        for value in _list(
            spec.get("expected_order")
            or _dict(spec.get("property")).get("expected_order")
        )
        if _text(value)
    ]

    # Async evidence is evaluated first because a complete violation is a
    # settled business verdict, independent of what a downstream node later did.
    if async_rows.get("coverage_complete") is not True:
        return {
            "passed": None,
            "reason_code": "PROCESS_GRAPH_EVENT_OBSERVATION_INCOMPLETE",
            "expected": {
                "declared_transition_count": int(
                    async_rows.get("declared_transition_count") or 0
                )
            },
            "actual": {
                "observed_transition_count": int(
                    async_rows.get("observed_transition_count") or 0
                ),
                "issues": _list(async_rows.get("issues")),
            },
        }

    transitions = [
        _dict(row)
        for row in _list(async_rows.get("transitions"))
        if isinstance(row, dict)
    ]
    violations = [
        row
        for row in transitions
        if _text(row.get("semantic_status")) == "VIOLATION"
    ]
    if violations:
        all_reason_codes = list(
            dict.fromkeys(
                _text(reason)
                for row in violations
                for reason in (
                    _list(row.get("semantic_reason_codes"))
                    or [_text(row.get("reason_code"))]
                )
                if _text(reason)
            )
        )
        return {
            "passed": False,
            "reason_code": (
                all_reason_codes[0]
                if all_reason_codes
                else "PROCESS_GRAPH_ASYNC_TRANSITION_VIOLATION"
            ),
            "expected": {
                "all_async_transitions": "PASS",
                "transition_count": len(transitions),
            },
            "actual": {
                "violating_step_ids": [
                    _text(row.get("step_id")) for row in violations
                ],
                "violation_reason_codes": all_reason_codes,
                "broker_violation_reason_codes": list(
                    dict.fromkeys(
                        _text(reason)
                        for row in violations
                        for reason in _list(row.get("broker_reason_codes"))
                        if _text(reason)
                    )
                ),
            },
        }

    observed_order = [
        _text(value)
        for value in _list(process.get("observed_order"))
        if _text(value)
    ]
    unreached = [
        _text(value)
        for value in _list(process.get("steps_not_reaching_transport"))
        if _text(value)
    ]
    missing = [
        step_id for step_id in expected_steps if step_id not in observed_order
    ]
    if (
        not process
        or process.get("coverage_complete") is not True
        or missing
        or unreached
    ):
        return {
            "passed": None,
            "reason_code": "PROCESS_COVERAGE_INCOMPLETE",
            "expected": {"expected_steps": expected_steps},
            "actual": {
                "observed_order": observed_order,
                "missing_steps": missing,
                "steps_not_reaching_transport": unreached,
            },
        }
    if expected_order:
        expected_set = set(expected_order)
        observed_declared = [
            step_id for step_id in observed_order if step_id in expected_set
        ]
        if observed_declared != expected_order:
            return {
                "passed": False,
                "reason_code": "PROCESS_STEP_ORDER_VIOLATION",
                "expected": {"expected_order": expected_order},
                "actual": {"observed_order": observed_declared},
            }

    return {
        "passed": True,
        "reason_code": "",
        "expected": {
            "expected_steps": expected_steps,
            "all_async_transitions": "PASS",
        },
        "actual": {
            "observed_order": observed_order,
            "async_transition_count": len(transitions),
        },
    }


def install_process_graph_async_transition_surface() -> dict[str, str]:
    from .assertion_dsl_base import (
        register_assertion_kind,
        registered_assertion_kinds,
    )
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=observe_async_transitions,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=evaluate_process_async_completion,
            required_evidence_keys=(
                "process_step_timeline",
                EVIDENCE_KEY,
            ),
        )
    else:
        installed["assertion"] = ASSERTION_KIND
    return installed


__all__ = [
    "OBSERVER_ID",
    "SURFACE",
    "ADAPTER",
    "EVIDENCE_KEY",
    "ASSERTION_KIND",
    "observe_async_transitions",
    "evaluate_process_async_completion",
    "install_process_graph_async_transition_surface",
]
