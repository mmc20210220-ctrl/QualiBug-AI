from __future__ import annotations

import json
from copy import deepcopy

from ai_test_asset_center.process_graph_broker_adapter import (
    BROKER_ADAPTER_CAPABILITY_MISSING,
    BROKER_ADAPTER_NON_DESTRUCTIVE_VIOLATION,
    BROKER_ADAPTER_SCOPE_MISMATCH,
    BROKER_ADAPTER_UNAVAILABLE,
    RECEIPT_SCHEMA_VERSION,
)
from ai_test_asset_center.process_graph_broker_delivery import BROKER_TOPIC_MISMATCH
from ai_test_asset_center.process_graph_wait_contract import (
    STATUS_BLOCKED,
    STATUS_COMPILED,
    STATUS_CONVERGED,
    compile_process_graph_wait_contracts,
    compiled_wait_runtime_ready,
    execute_process_graph_wait,
)


IR = {
    "operations": [
        {
            "id": "op_submit",
            "method": "POST",
            "path": "/orders",
            "system_ref": "orders",
        },
        {
            "id": "op_consume",
            "method": "GET",
            "path": "/notifications/{order_id}",
            "system_ref": "notifications",
        },
    ]
}


def _graph() -> dict:
    return {
        "execution_graph_id": "graph_direct_broker",
        "process_id": "process_direct_broker",
        "nodes": [
            {
                "node_id": "submit",
                "operation_ref": "op_submit",
                "actor_ref": "actor_service",
                "system_ref": "orders",
                "method": "POST",
                "path": "/orders",
            },
            {
                "node_id": "consume",
                "operation_ref": "op_consume",
                "actor_ref": "actor_service",
                "system_ref": "notifications",
                "method": "GET",
                "path": "/notifications/{order_id}",
            },
        ],
        "edges": [
            {
                "edge_id": "edge_order_event",
                "source_node_id": "submit",
                "target_node_id": "consume",
                "relation_type": "MESSAGE",
            }
        ],
        "topological_order": ["submit", "consume"],
        "wait_contracts": [
            {
                "wait_id": "wait_order_event",
                "source_node_id": "submit",
                "target_node_id": "consume",
                "actor_ref": "actor_service",
                "system_ref": "notifications",
                "async_policy": {
                    "enabled": True,
                    "expected_max_delay_ms": 20,
                    "poll_interval_ms": 1,
                    "max_attempts": 2,
                    "required_stable_observations": 1,
                    "terminal_condition": "source_declared_event_delivery",
                },
                "event_transition": {
                    "delivery_kind": "message",
                    "delivery_semantics": "at_least_once",
                    "events_path": "$.items",
                    "event_id_field": "$.event_id",
                    "event_type_field": "$.event_type",
                    "correlation_field": "$.order_id",
                    "correlation_binding": "order_id",
                    "expected_event_type": "OrderCreated",
                    "expected_min_count": 1,
                    "expected_max_count": 2,
                    "source_refs": [
                        {
                            "source_id": "event-contract",
                            "kind": "formal_event_contract",
                            "locator": "OrderCreated",
                        }
                    ],
                    "broker_delivery": {
                        "broker_model": "partitioned_log",
                        "topic_field": "$.broker.topic",
                        "expected_topic": "orders.events",
                        "partition_field": "$.broker.partition",
                        "offset_field": "$.broker.offset",
                        "checkpoint_field": "$.broker.checkpoint",
                        "consumer_group_field": "$.broker.consumer_group",
                        "expected_consumer_group": "notification-service",
                        "delivery_state_field": "$.broker.state",
                        "dead_letter_topic_field": "$.broker.dead_letter_topic",
                        "checkpoint_policy": "must_cover_observed",
                        "dlq_policy": "forbidden",
                        "dead_letter_states": ["DLQ", "DEAD_LETTERED"],
                        "ordering_policy": "partition_offset",
                        "restart_deduplication_required": False,
                        "source_refs": [
                            {
                                "source_id": "broker-contract",
                                "kind": "formal_broker_contract",
                                "locator": "orders.events/notification-service",
                            }
                        ],
                    },
                    "direct_broker_adapter": {
                        "adapter_kind": "kafka",
                        "adapter_ref": "broker-adapter:orders-kafka",
                        "runtime_capability_ref": "runtime-capability:orders-kafka-read",
                        "observation_mode": "read_only_snapshot",
                        "consumer_isolation": "broker_admin_snapshot",
                        "commit_mode": "none",
                        "acknowledgment_mode": "none",
                        "max_records_per_poll": 50,
                        "required_capabilities": [
                            "records",
                            "checkpoints",
                            "delivery_confirmations",
                            "dlq",
                            "rebalances",
                        ],
                        "require_delivery_confirmation_receipts": True,
                        "require_rebalance_receipts": True,
                        "source_refs": [
                            {
                                "source_id": "runtime-settings",
                                "kind": "broker_adapter_contract",
                                "locator": "orders-kafka-read",
                            }
                        ],
                    },
                },
            }
        ],
    }


def _compile(graph: dict | None = None) -> dict:
    result = compile_process_graph_wait_contracts(graph or _graph(), behavior_ir=IR)
    assert result["status"] == STATUS_COMPILED, result
    return result["graph"]


def _record(*, topic: str = "orders.events") -> dict:
    return {
        "event": {
            "event_id": "evt-1",
            "event_type": "OrderCreated",
            "order_id": "ORD-42",
            "payload": {"status": "created"},
        },
        "broker": {
            "topic": topic,
            "partition": 2,
            "offset": 10,
            "checkpoint": 10,
            "consumer_group": "notification-service",
            "state": "PROCESSED",
            "dead_letter_topic": "",
        },
    }


def _adapter_receipt(
    graph: dict,
    *,
    topic: str = "orders.events",
    non_destructive: bool = True,
    capabilities: list[str] | None = None,
) -> dict:
    event = graph["wait_contracts_by_target"]["consume"][
        "event_transition_contract"
    ]
    adapter = event["broker_read_adapter_contract"]
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": "OBSERVED",
        "adapter_kind": "kafka",
        "adapter_ref": "broker-adapter:orders-kafka",
        "contract_fingerprint": adapter["contract_fingerprint"],
        "capabilities": capabilities
        or [
            "records",
            "checkpoints",
            "delivery_confirmations",
            "dlq",
            "rebalances",
        ],
        "non_destructive": non_destructive,
        "commit_performed": False,
        "ack_performed": False,
        "nack_performed": False,
        "records": [_record(topic=topic)],
        "checkpoint_receipts": [
            {"partition": 2, "committed_offset": 10}
        ],
        "delivery_confirmation_receipts": [
            {"state": "ACK", "delivery_identity": "delivery-1"}
        ],
        "dlq_receipts": [{"count": 0}],
        "rebalance_receipts": [{"epoch": "assignment-7", "state": "STABLE"}],
    }


def _execute(graph: dict, receipt: dict | None = None) -> dict:
    rows = iter([receipt or _adapter_receipt(graph), receipt or _adapter_receipt(graph)])
    ticks = iter([0.0, 0.0, 0.001, 0.002, 0.003, 0.004])
    return execute_process_graph_wait(
        graph=graph,
        step=next(row for row in graph["nodes"] if row["node_id"] == "consume"),
        context={"bindings": {"order_id": "ORD-42"}},
        actors={"actor_service": {"role": "service"}},
        tokens={},
        read_once=lambda: next(rows),
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    )


def test_direct_adapter_compiles_without_http_observer_operation() -> None:
    graph = _compile()
    wait = graph["wait_contracts_by_target"]["consume"]
    event = wait["event_transition_contract"]
    adapter = event["broker_read_adapter_contract"]

    assert wait["observer_transport_kind"] == "broker_adapter"
    assert wait["observer_adapter_ref"] == "broker-adapter:orders-kafka"
    assert wait["observer_operation_ref"] == ""
    assert wait["method"] == ""
    assert wait["path_template"] == ""
    assert event["observer_transport_kind"] == "broker_adapter"
    assert event["correlation_query_parameter"] == ""
    assert adapter["adapter_kind"] == "kafka"
    assert graph["wait_runtime_contract"]["broker_adapter_count"] == 1
    assert compiled_wait_runtime_ready(graph) == (True, "")


def test_direct_adapter_and_http_observer_are_compile_time_ambiguous() -> None:
    graph = _graph()
    graph["wait_contracts"][0]["observer_operation_ref"] = "op_consume"

    result = compile_process_graph_wait_contracts(graph, behavior_ir=IR)

    assert result["status"] == STATUS_BLOCKED
    assert "direct_broker_adapter_and_http_observer_are_ambiguous" in result["detail"]


def test_adapter_requires_its_own_source_refs() -> None:
    graph = _graph()
    graph["wait_contracts"][0]["event_transition"]["direct_broker_adapter"][
        "source_refs"
    ] = []

    result = compile_process_graph_wait_contracts(graph, behavior_ir=IR)

    assert result["status"] == STATUS_BLOCKED
    assert "broker_adapter_source_refs_missing" in result["detail"]


def test_direct_adapter_receipt_reuses_existing_event_and_broker_oracles() -> None:
    graph = _compile()

    receipt = _execute(graph)

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "PASS"
    assert receipt["observer_transport_kind"] == "broker_adapter"
    assert receipt["broker_adapter_status"] == "OBSERVED"
    assert receipt["poll_replay_count"] == 1
    evidence = receipt["broker_adapter_evidence"]
    assert evidence["observation_count"] == 2
    assert evidence["record_count"] == 2
    assert evidence["checkpoint_receipt_count"] == 2
    assert evidence["delivery_confirmation_receipt_count"] == 2
    assert evidence["rebalance_receipt_count"] == 2
    assert evidence["ack_count"] == 2


def test_adapter_wrong_topic_is_still_a_business_oracle_violation() -> None:
    graph = _compile()
    adapter_receipt = _adapter_receipt(graph, topic="payments.events")

    receipt = _execute(graph, adapter_receipt)

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "VIOLATION"
    assert BROKER_TOPIC_MISMATCH in receipt["semantic_reason_codes"]
    assert receipt["broker_adapter_status"] == "OBSERVED"


def test_missing_runtime_adapter_blocks_before_observation() -> None:
    graph = _compile()

    receipt = execute_process_graph_wait(
        graph=graph,
        step={"node_id": "consume"},
        context={"bindings": {"order_id": "ORD-42"}},
        actors={},
        tokens={},
    )

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["semantic_status"] == "INDETERMINATE"
    assert receipt["reason_code"] == BROKER_ADAPTER_UNAVAILABLE
    assert receipt["broker_adapter_status"] == "INDETERMINATE"


def test_adapter_side_effect_or_missing_capability_is_fail_closed() -> None:
    graph = _compile()
    destructive = _adapter_receipt(graph, non_destructive=False)
    destructive_result = _execute(graph, destructive)
    assert destructive_result["status"] == STATUS_BLOCKED
    assert (
        destructive_result["reason_code"]
        == BROKER_ADAPTER_NON_DESTRUCTIVE_VIOLATION
    )

    incomplete = _adapter_receipt(
        graph,
        capabilities=["records", "checkpoints", "dlq", "rebalances"],
    )
    incomplete_result = _execute(graph, incomplete)
    assert incomplete_result["status"] == STATUS_BLOCKED
    assert incomplete_result["reason_code"] == BROKER_ADAPTER_CAPABILITY_MISSING


def test_adapter_receipt_scope_mismatch_is_fail_closed() -> None:
    graph = _compile()
    raw = _adapter_receipt(graph)
    raw["contract_fingerprint"] = "drifted"

    receipt = _execute(graph, raw)

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["reason_code"] == BROKER_ADAPTER_SCOPE_MISMATCH


def test_adapter_receipt_redacts_broker_and_runtime_identity_values() -> None:
    graph = _compile()
    raw = _adapter_receipt(graph)

    receipt = _execute(graph, raw)
    rendered = json.dumps(receipt, ensure_ascii=False, sort_keys=True)

    assert receipt["semantic_status"] == "PASS"
    for secret in (
        "orders.events",
        "notification-service",
        "broker-adapter:orders-kafka",
        "runtime-capability:orders-kafka-read",
        "delivery-1",
        "assignment-7",
    ):
        assert secret not in rendered
    assert "adapter_ref_fingerprint" in receipt["broker_adapter_evidence"]


def test_nested_adapter_contract_drift_blocks_runtime_readiness() -> None:
    graph = _compile()
    drifted = deepcopy(graph)
    drifted["wait_contracts_by_target"]["consume"][
        "event_transition_contract"
    ]["broker_read_adapter_contract"]["adapter_kind"] = "rabbitmq"

    ready, detail = compiled_wait_runtime_ready(drifted)

    assert ready is False
    assert detail in {
        "wait_contract_target_index_content_mismatch",
        "wait_contract_fingerprint_drift",
        "event_transition_contract_drift",
        "broker_adapter_contract_drift",
    }
