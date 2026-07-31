from __future__ import annotations

import json
from copy import deepcopy

from ai_test_asset_center.process_graph_wait_contract import (
    EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM,
    EVENT_IDEMPOTENCY_KEY_MISMATCH,
    EVENT_TRANSITION_INVALID,
    STATUS_BLOCKED,
    STATUS_COMPILED,
    STATUS_CONVERGED,
    compile_process_graph_wait_contracts,
    compiled_wait_runtime_ready,
    execute_process_graph_wait,
)


def _ir() -> dict:
    return {
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
            {
                "id": "op_event_observer",
                "method": "GET",
                "path": "/test-observers/events",
                "system_ref": "notifications",
            },
        ]
    }


def _graph() -> dict:
    return {
        "execution_graph_id": "graph_event_1",
        "process_id": "process_event_1",
        "nodes": [
            {
                "node_id": "submit_order",
                "step_id": "submit_order",
                "operation_ref": "op_submit",
                "actor_ref": "actor_1",
                "system_ref": "orders",
                "method": "POST",
                "path": "/orders",
            },
            {
                "node_id": "consume_notification",
                "step_id": "consume_notification",
                "operation_ref": "op_consume",
                "actor_ref": "actor_1",
                "system_ref": "notifications",
                "method": "GET",
                "path": "/notifications/{order_id}",
            },
        ],
        "edges": [
            {
                "edge_id": "edge_event_1",
                "source_node_id": "submit_order",
                "target_node_id": "consume_notification",
                "relation_type": "MESSAGE",
            }
        ],
        "topological_order": ["submit_order", "consume_notification"],
        "wait_contracts": [
            {
                "wait_id": "wait_order_created",
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
                    "max_attempts": 3,
                    "required_stable_observations": 1,
                    "terminal_condition": "source_declared_event_delivery",
                },
                "event_transition": {
                    "delivery_kind": "message",
                    "delivery_semantics": "exactly_once",
                    "events_path": "$.items",
                    "event_id_field": "$.event_id",
                    "event_type_field": "$.event_type",
                    "correlation_field": "$.aggregate_id",
                    "correlation_binding": "order_id",
                    "correlation_query_parameter": "aggregate_id",
                    "expected_event_type": "OrderCreated",
                    "expected_min_count": 1,
                    "expected_max_count": 1,
                    "idempotency_key_binding": "request_id",
                    "idempotency_key_field": "$.idempotency_key",
                    "delivery_attempt_field": "$.delivery_attempt",
                    "expected_max_delivery_attempt": 2,
                    "source_refs": [
                        {
                            "kind": "formal_event_contract",
                            "locator": "events:OrderCreated",
                        }
                    ],
                },
            }
        ],
    }


def _compiled_graph() -> dict:
    result = compile_process_graph_wait_contracts(_graph(), behavior_ir=_ir())
    assert result["status"] == STATUS_COMPILED, result
    return result["graph"]


def _event(event_id: str, *, idempotency_key: str = "REQ-1") -> dict:
    return {
        "event_id": event_id,
        "event_type": "OrderCreated",
        "aggregate_id": "ORD-42",
        "idempotency_key": idempotency_key,
        "delivery_attempt": 1,
        "payload": {"order_id": "ORD-42"},
    }


def _execute(graph: dict, responses: list[dict]) -> dict:
    iterator = iter(responses)
    ticks = iter([0.0, 0.0, 0.001, 0.002, 0.003])
    step = next(
        row
        for row in graph["nodes"]
        if row["node_id"] == "consume_notification"
    )
    return execute_process_graph_wait(
        graph=graph,
        step=step,
        context={
            "base_url": "https://notifications.example.test",
            "bindings": {"order_id": "ORD-42", "request_id": "REQ-1"},
        },
        actors={"actor_1": {"role": "public"}},
        tokens={},
        read_once=lambda: next(iterator),
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    )


def test_event_transition_compiles_behind_existing_wait_contract() -> None:
    graph = _compiled_graph()
    wait = graph["wait_contracts_by_target"]["consume_notification"]
    event = wait["event_transition_contract"]
    assert wait["transition_kind"] == "event_delivery"
    assert event["delivery_kind"] == "message"
    assert event["delivery_semantics"] == "exactly_once"
    assert event["correlation_binding"] == "order_id"
    assert event["idempotency_key_binding"] == "request_id"
    assert graph["wait_runtime_contract"]["event_transition_count"] == 1
    assert compiled_wait_runtime_ready(graph) == (True, "")


def test_missing_stable_event_identity_blocks_compile() -> None:
    graph = _graph()
    del graph["wait_contracts"][0]["event_transition"]["event_id_field"]
    result = compile_process_graph_wait_contracts(graph, behavior_ir=_ir())
    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == EVENT_TRANSITION_INVALID
    assert "event_id_field" in result["detail"]


def test_event_contract_drift_blocks_runtime_before_transport() -> None:
    graph = _compiled_graph()
    wait = graph["wait_contracts"][0]
    wait["event_transition_contract"]["expected_event_type"] = "PaymentCaptured"
    graph["wait_contracts_by_target"]["consume_notification"] = deepcopy(wait)
    ready, detail = compiled_wait_runtime_ready(graph)
    assert ready is False
    assert detail == "wait_contract_fingerprint_drift"


def test_wait_target_index_content_drift_is_rejected() -> None:
    graph = _compiled_graph()
    graph["wait_contracts_by_target"]["consume_notification"][
        "observer_path_template"
    ] = "/other"
    assert compiled_wait_runtime_ready(graph) == (
        False,
        "wait_contract_target_index_content_mismatch",
    )


def test_same_event_seen_by_every_poll_is_one_delivery_not_duplicate() -> None:
    graph = _compiled_graph()
    response = {"status_code": 200, "body": {"items": [_event("evt-1")]}}
    receipt = _execute(graph, [response, response, response])
    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "PASS"
    assert receipt["observed_matching_row_count"] == 3
    assert receipt["observed_unique_event_count"] == 1
    assert receipt["poll_replay_count"] == 2
    assert receipt["distinct_delivery_overflow_count"] == 0
    assert receipt["coverage_complete"] is True
    serialized = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
    assert "evt-1" not in serialized
    assert "ORD-42" not in serialized
    assert "REQ-1" not in serialized


def test_distinct_event_ids_are_converged_observation_with_violation() -> None:
    graph = _compiled_graph()
    receipt = _execute(
        graph,
        [
            {"status_code": 200, "body": {"items": [_event("evt-1")]}},
            {
                "status_code": 200,
                "body": {"items": [_event("evt-1"), _event("evt-2")]},
            },
            {
                "status_code": 200,
                "body": {"items": [_event("evt-1"), _event("evt-2")]},
            },
        ],
    )
    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "VIOLATION"
    assert receipt["reason_code"] == EVENT_DELIVERY_COUNT_ABOVE_MAXIMUM
    assert receipt["observed_unique_event_count"] == 2
    assert receipt["distinct_delivery_overflow_count"] == 1
    assert receipt["converged"] is True


def test_event_idempotency_violation_is_not_harness_block() -> None:
    graph = _compiled_graph()
    response = {
        "status_code": 200,
        "body": {"items": [_event("evt-1", idempotency_key="OTHER")]},
    }
    receipt = _execute(graph, [response, response, response])
    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "VIOLATION"
    assert receipt["reason_code"] == EVENT_IDEMPOTENCY_KEY_MISMATCH
    assert receipt["idempotency_mismatch_count"] == 1
