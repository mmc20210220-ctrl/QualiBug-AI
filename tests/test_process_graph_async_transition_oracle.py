from __future__ import annotations

from ai_test_asset_center.multi_step_protocol import (
    compile_multi_step_process_protocol,
)
from ai_test_asset_center.process_graph_async_transition_observer import (
    ASSERTION_KIND,
    EVIDENCE_KEY,
    OBSERVER_ID,
    evaluate_process_async_completion,
    observe_async_transitions,
)
from ai_test_asset_center.process_graph_event_transition import (
    RECEIPT_SCHEMA_VERSION,
)


def _event_contract() -> dict:
    return {
        "contract_fingerprint": "event_contract_fp_1",
        "source_node_id": "submit_order",
        "target_node_id": "consume_notification",
        "edge_id": "edge_order_notification",
    }


def _experiment() -> dict:
    return {
        "execution_graph": {
            "wait_contracts": [
                {
                    "target_node_id": "consume_notification",
                    "transition_kind": "event_delivery",
                    "event_transition_contract": _event_contract(),
                }
            ]
        }
    }


def _runtime_receipt(*, semantic_status: str, reason_code: str = "") -> dict:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": "event_receipt_1",
        "contract_fingerprint": "event_contract_fp_1",
        "step_id": "consume_notification",
        "source_node_id": "submit_order",
        "target_node_id": "consume_notification",
        "delivery_kind": "message",
        "delivery_semantics": "exactly_once",
        "semantic_status": semantic_status,
        "reason_code": reason_code,
        "coverage_complete": True,
        "observation_window_completed": True,
        "attempt_count": 3,
        "observed_unique_event_count": 2 if reason_code else 1,
        "distinct_delivery_overflow_count": 1 if reason_code else 0,
        "event_id_reuse_conflict_count": 0,
        "event_identity_type_conflict_count": 0,
        "correlation_identity_mismatch_count": 0,
        "idempotency_mismatch_count": 0,
        "retry_limit_violation_count": 0,
    }


def _process_observation() -> dict:
    return {
        "coverage_complete": True,
        "observed_order": ["submit_order", "consume_notification"],
        "steps_not_reaching_transport": [],
    }


def _async_evidence(*, semantic_status: str, reason_code: str = "") -> dict:
    return {
        "coverage_complete": True,
        "declared_transition_count": 1,
        "observed_transition_count": 1,
        "transitions": [
            {
                "step_id": "consume_notification",
                "semantic_status": semantic_status,
                "reason_code": reason_code,
            }
        ],
    }


def test_async_transition_observer_keeps_exact_contract_scope() -> None:
    receipt = observe_async_transitions(
        {
            "experiment": _experiment(),
            "observations": {
                "process_graph_async_transition_receipts": [
                    _runtime_receipt(semantic_status="PASS")
                ]
            },
        }
    )
    assert receipt["status"] == "OBSERVED"
    evidence = receipt["evidence"][EVIDENCE_KEY]
    assert evidence["coverage_complete"] is True
    assert evidence["transitions"][0]["step_id"] == "consume_notification"
    assert evidence["transitions"][0]["semantic_status"] == "PASS"


def test_async_transition_observer_preserves_typed_identity_evidence() -> None:
    runtime = _runtime_receipt(
        semantic_status="VIOLATION",
        reason_code="PROCESS_GRAPH_EVENT_CORRELATION_IDENTITY_MISMATCH",
    )
    runtime["correlation_identity_mismatch_count"] = 1
    runtime["event_identity_type_conflict_count"] = 2
    receipt = observe_async_transitions(
        {
            "experiment": _experiment(),
            "observations": {
                "process_graph_async_transition_receipts": [runtime]
            },
        }
    )
    transition = receipt["evidence"][EVIDENCE_KEY]["transitions"][0]
    assert transition["correlation_identity_mismatch_count"] == 1
    assert transition["event_identity_type_conflict_count"] == 2


def test_async_transition_observer_rejects_receipt_contract_drift() -> None:
    runtime = _runtime_receipt(semantic_status="PASS")
    runtime["contract_fingerprint"] = "drifted"
    receipt = observe_async_transitions(
        {
            "experiment": _experiment(),
            "observations": {
                "process_graph_async_transition_receipts": [runtime]
            },
        }
    )
    assert receipt["status"] == "INDETERMINATE"
    evidence = receipt["evidence"][EVIDENCE_KEY]
    assert evidence["coverage_complete"] is False
    assert "receipt_contract_scope_mismatch" in evidence["issues"][0]


def test_combined_assertion_reports_measured_event_violation() -> None:
    result = evaluate_process_async_completion(
        {
            "spec": {
                "expected_steps": ["submit_order", "consume_notification"],
                "expected_order": ["submit_order", "consume_notification"],
            },
            "observations": {
                "process_step_timeline": _process_observation(),
                EVIDENCE_KEY: _async_evidence(
                    semantic_status="VIOLATION",
                    reason_code=(
                        "PROCESS_GRAPH_EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM"
                    ),
                ),
            },
        }
    )
    assert result["passed"] is False
    assert result["reason_code"] == (
        "PROCESS_GRAPH_EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM"
    )


def test_proven_event_violation_survives_incomplete_downstream_process() -> None:
    result = evaluate_process_async_completion(
        {
            "spec": {
                "expected_steps": ["submit_order", "consume_notification"],
                "expected_order": ["submit_order", "consume_notification"],
            },
            "observations": {
                "process_step_timeline": {
                    "coverage_complete": False,
                    "observed_order": ["submit_order"],
                    "steps_not_reaching_transport": ["consume_notification"],
                },
                EVIDENCE_KEY: _async_evidence(
                    semantic_status="VIOLATION",
                    reason_code="PROCESS_GRAPH_EVENT_IDEMPOTENCY_KEY_MISMATCH",
                ),
            },
        }
    )
    assert result["passed"] is False
    assert result["reason_code"] == (
        "PROCESS_GRAPH_EVENT_IDEMPOTENCY_KEY_MISMATCH"
    )


def test_combined_assertion_passes_only_complete_process_and_events() -> None:
    result = evaluate_process_async_completion(
        {
            "spec": {
                "expected_steps": ["submit_order", "consume_notification"],
                "expected_order": ["submit_order", "consume_notification"],
            },
            "observations": {
                "process_step_timeline": _process_observation(),
                EVIDENCE_KEY: _async_evidence(semantic_status="PASS"),
            },
        }
    )
    assert result["passed"] is True


def test_wait_capable_protocol_selects_async_observer_and_assertion() -> None:
    envelope = {
        "risk_family": "process",
        "operation": {
            "id": "op_read_order",
            "method": "GET",
            "path": "/orders/{order_id}",
        },
        "operation_ref": "op_read_order",
        "control_actor_ref": "",
        "treatment_actor_ref": "actor_1",
        "property_spec": {
            "process_graph": {
                "process_id": "order_notification",
                "nodes": [
                    {
                        "node_id": "submit_order",
                        "operation_ref": "op_read_order",
                        "actor_ref": "actor_1",
                        "system_ref": "orders",
                        "method": "GET",
                        "path": "/orders/{order_id}",
                    },
                    {
                        "node_id": "consume_notification",
                        "operation_ref": "op_read_notification",
                        "actor_ref": "actor_1",
                        "system_ref": "notifications",
                        "method": "GET",
                        "path": "/notifications/{order_id}",
                    },
                ],
                "edges": [
                    {
                        "edge_id": "edge_order_notification",
                        "source_node_id": "submit_order",
                        "target_node_id": "consume_notification",
                        "relation_type": "MESSAGE",
                    }
                ],
                "topological_order": [
                    "submit_order",
                    "consume_notification",
                ],
                "wait_contracts": [
                    {
                        "wait_id": "wait_order_notification",
                        "source_node_id": "submit_order",
                        "target_node_id": "consume_notification",
                        "observer_operation_ref": "op_event_observer",
                        "actor_ref": "actor_1",
                        "system_ref": "notifications",
                        "predicate": {"status_codes": [200]},
                        "async_policy": {
                            "enabled": True,
                            "expected_max_delay_ms": 10,
                            "poll_interval_ms": 1,
                            "max_attempts": 2,
                            "required_stable_observations": 1,
                            "terminal_condition": "source_declared_event_delivery",
                        },
                        "event_transition": {
                            "delivery_kind": "message",
                            "delivery_semantics": "exactly_once",
                            "events_path": "$.items",
                            "event_id_field": "$.event_id",
                            "event_type_field": "$.event_type",
                            "correlation_field": "$.order_id",
                            "correlation_binding": "order_id",
                            "correlation_query_parameter": "order_id",
                            "expected_event_type": "OrderCreated",
                            "expected_min_count": 1,
                            "expected_max_count": 1,
                            "source_refs": [
                                {
                                    "source_id": "event_spec",
                                    "kind": "formal_event_contract",
                                    "locator": "OrderCreated",
                                }
                            ],
                        },
                    }
                ],
            }
        },
        "behavior_ir": {
            "operations": [
                {
                    "id": "op_read_order",
                    "method": "GET",
                    "path": "/orders/{order_id}",
                    "system_ref": "orders",
                },
                {
                    "id": "op_read_notification",
                    "method": "GET",
                    "path": "/notifications/{order_id}",
                    "system_ref": "notifications",
                },
                {
                    "id": "op_event_observer",
                    "method": "GET",
                    "path": "/test-observers/events",
                    "system_ref": "notifications",
                },
            ],
            "relations": [],
            "entities": [],
        },
    }
    result = compile_multi_step_process_protocol(envelope)
    assert result["status"] == "COMPILED", result
    assert result["assertion"]["kind"] == ASSERTION_KIND
    observer_ids = {row["observer_id"] for row in result["observers"]}
    assert OBSERVER_ID in observer_ids
    assert "process_step_timeline" in observer_ids
    assert result["wait_runtime_contract"]["event_transition_count"] == 1
