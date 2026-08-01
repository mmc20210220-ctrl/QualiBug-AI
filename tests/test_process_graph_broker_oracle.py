from __future__ import annotations

from ai_test_asset_center.process_graph_async_transition_observer import (
    EVIDENCE_KEY,
    evaluate_process_async_completion,
    observe_async_transitions,
)
from ai_test_asset_center.process_graph_broker_delivery import (
    BROKER_DLQ_DELIVERY_UNEXPECTED,
    BROKER_TOPIC_MISMATCH,
)
from ai_test_asset_center.process_graph_event_transition import (
    RECEIPT_SCHEMA_VERSION,
)


def _experiment() -> dict:
    return {
        "execution_graph": {
            "wait_contracts": [
                {
                    "target_node_id": "consume_event",
                    "transition_kind": "event_delivery",
                    "event_transition_contract": {
                        "contract_fingerprint": "event-fp",
                        "source_node_id": "submit_order",
                        "target_node_id": "consume_event",
                        "edge_id": "edge-order-event",
                        "broker_delivery_contract": {
                            "contract_fingerprint": "broker-fp",
                        },
                    },
                }
            ]
        }
    }


def _runtime_receipt() -> dict:
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "receipt_id": "event-receipt-1",
        "contract_fingerprint": "event-fp",
        "broker_contract_fingerprint": "broker-fp",
        "step_id": "consume_event",
        "source_node_id": "submit_order",
        "target_node_id": "consume_event",
        "delivery_kind": "message",
        "delivery_semantics": "at_least_once",
        "semantic_status": "VIOLATION",
        "reason_code": BROKER_TOPIC_MISMATCH,
        "semantic_reason_codes": [
            BROKER_TOPIC_MISMATCH,
            BROKER_DLQ_DELIVERY_UNEXPECTED,
        ],
        "coverage_complete": True,
        "observation_window_completed": True,
        "attempt_count": 3,
        "observed_unique_event_count": 1,
        "distinct_delivery_overflow_count": 0,
        "event_id_reuse_conflict_count": 0,
        "idempotency_mismatch_count": 0,
        "retry_limit_violation_count": 0,
        "broker_semantic_status": "VIOLATION",
        "broker_reason_codes": [
            BROKER_TOPIC_MISMATCH,
            BROKER_DLQ_DELIVERY_UNEXPECTED,
        ],
        "broker_evidence": {
            "contract_fingerprint": "broker-fp",
            "broker_model": "partitioned_log",
            "topic_mismatch_count": 1,
            "dlq_delivery_count": 1,
            "unexpected_dlq_delivery_count": 1,
            "topic_fingerprints": ["topic-hash"],
            "consumer_group_fingerprints": ["group-hash"],
            "checkpoint_state_fingerprint": "checkpoint-hash",
        },
    }


def test_broker_receipt_scope_and_counts_reach_oracle() -> None:
    observer = observe_async_transitions(
        {
            "experiment": _experiment(),
            "observations": {
                "process_graph_async_transition_receipts": [
                    _runtime_receipt()
                ]
            },
        }
    )

    assert observer["status"] == "OBSERVED"
    transition = observer["evidence"][EVIDENCE_KEY]["transitions"][0]
    assert transition["broker_contract_fingerprint"] == "broker-fp"
    assert transition["broker_evidence"]["topic_mismatch_count"] == 1
    assert transition["broker_evidence"]["unexpected_dlq_delivery_count"] == 1

    verdict = evaluate_process_async_completion(
        {
            "spec": {
                "expected_steps": ["submit_order", "consume_event"],
                "expected_order": ["submit_order", "consume_event"],
            },
            "observations": {
                "process_step_timeline": {
                    "coverage_complete": True,
                    "observed_order": ["submit_order", "consume_event"],
                    "steps_not_reaching_transport": [],
                },
                EVIDENCE_KEY: observer["evidence"][EVIDENCE_KEY],
            },
        }
    )

    assert verdict["passed"] is False
    assert verdict["reason_code"] == BROKER_TOPIC_MISMATCH
    assert verdict["actual"]["violation_reason_codes"] == [
        BROKER_TOPIC_MISMATCH,
        BROKER_DLQ_DELIVERY_UNEXPECTED,
    ]
    assert verdict["actual"]["broker_violation_reason_codes"] == [
        BROKER_TOPIC_MISMATCH,
        BROKER_DLQ_DELIVERY_UNEXPECTED,
    ]


def test_broker_contract_fingerprint_drift_is_indeterminate() -> None:
    receipt = _runtime_receipt()
    receipt["broker_contract_fingerprint"] = "drifted"

    observer = observe_async_transitions(
        {
            "experiment": _experiment(),
            "observations": {
                "process_graph_async_transition_receipts": [receipt]
            },
        }
    )

    assert observer["status"] == "INDETERMINATE"
    evidence = observer["evidence"][EVIDENCE_KEY]
    assert evidence["coverage_complete"] is False
    assert "receipt_contract_scope_mismatch" in evidence["issues"][0]
