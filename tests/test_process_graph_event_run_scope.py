from __future__ import annotations

from ai_test_asset_center.process_graph_async_transition_observer import (
    EVIDENCE_KEY,
    observe_async_transitions,
)
from ai_test_asset_center.process_graph_event_transition import (
    EVENT_DELIVERY_COUNT_BELOW_MINIMUM,
    EVENT_IDEMPOTENCY_KEY_MISMATCH,
    RECEIPT_SCHEMA_VERSION,
    STATUS_CONVERGED,
)
from ai_test_asset_center.process_graph_wait_contract import (
    STATUS_COMPILED,
    compile_process_graph_wait_contracts,
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
        "execution_graph_id": "graph_event_scope",
        "process_id": "process_event_scope",
        "nodes": [
            {
                "node_id": "submit_order",
                "step_id": "submit_order",
                "operation_ref": "op_submit",
                "actor_ref": "actor_1",
                "system_ref": "orders",
                "method": "POST",
                "path": "/orders",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "order_id",
                        "json_path": "$.order_id",
                    }
                ],
            },
            {
                "node_id": "consume_notification",
                "step_id": "consume_notification",
                "operation_ref": "op_consume",
                "actor_ref": "actor_1",
                "system_ref": "notifications",
                "method": "GET",
                "path": "/notifications/{order_id}",
                "input_binding_refs": [
                    {
                        "producer_node_id": "submit_order",
                        "producer_output_field": "order_id",
                        "target": "order_id",
                    }
                ],
            },
        ],
        "edges": [
            {
                "edge_id": "edge_event_scope",
                "source_node_id": "submit_order",
                "target_node_id": "consume_notification",
                "relation_type": "MESSAGE",
                "binding_refs": [
                    {
                        "producer_node_id": "submit_order",
                        "consumer_node_id": "consume_notification",
                        "producer_output_field": "order_id",
                        "consumer_target": "order_id",
                    }
                ],
            }
        ],
        "topological_order": ["submit_order", "consume_notification"],
        "wait_contracts": [
            {
                "wait_id": "wait_event_scope",
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


def _event(event_id: str, idempotency_key: str | None) -> dict:
    row = {
        "event_id": event_id,
        "event_type": "OrderCreated",
        "aggregate_id": "ORD-42",
        "delivery_attempt": 1,
    }
    if idempotency_key is not None:
        row["idempotency_key"] = idempotency_key
    return row


def _execute(items: list[dict]) -> tuple[dict, dict]:
    graph = _compiled_graph()
    response = {"status_code": 200, "body": {"items": items}}
    responses = iter([response, response, response])
    ticks = iter([0.0, 0.0, 0.001, 0.002, 0.003])
    target = next(
        row for row in graph["nodes"] if row["node_id"] == "consume_notification"
    )
    receipt = execute_process_graph_wait(
        graph=graph,
        step=target,
        context={
            "base_url": "https://notifications.example.test",
            "bindings": {"order_id": "ORD-42", "request_id": "REQ-1"},
        },
        actors={"actor_1": {"role": "public"}},
        tokens={},
        read_once=lambda: next(responses),
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    )
    return graph, receipt


def test_historical_same_object_events_do_not_pollute_current_run() -> None:
    _graph_row, receipt = _execute(
        [
            _event("evt-old", "REQ-OLD"),
            _event("evt-current", "REQ-1"),
        ]
    )

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "PASS"
    assert receipt["event_scope_mode"] == "correlation_and_idempotency"
    assert receipt["observed_correlated_row_count"] == 6
    assert receipt["observed_matching_row_count"] == 3
    assert receipt["observed_unique_event_count"] == 1
    assert receipt["out_of_scope_idempotency_event_count"] == 1
    assert receipt["idempotency_mismatch_count"] == 0


def test_only_foreign_scope_events_cannot_satisfy_current_delivery() -> None:
    _graph_row, receipt = _execute([_event("evt-old", "REQ-OLD")])

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "VIOLATION"
    assert receipt["reason_code"] == EVENT_IDEMPOTENCY_KEY_MISMATCH
    assert EVENT_DELIVERY_COUNT_BELOW_MINIMUM in receipt["semantic_reason_codes"]
    assert receipt["observed_unique_event_count"] == 0
    assert receipt["out_of_scope_idempotency_event_count"] == 1
    assert receipt["idempotency_mismatch_count"] == 1


def test_missing_declared_idempotency_field_remains_a_violation() -> None:
    _graph_row, receipt = _execute([_event("evt-missing", None)])

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "VIOLATION"
    assert receipt["reason_code"] == EVENT_IDEMPOTENCY_KEY_MISMATCH
    assert receipt["missing_idempotency_event_count"] == 1
    assert receipt["observed_unique_event_count"] == 0


def test_observer_projects_run_scope_evidence() -> None:
    graph, receipt = _execute(
        [
            _event("evt-old", "REQ-OLD"),
            _event("evt-current", "REQ-1"),
        ]
    )
    assert receipt["schema_version"] == RECEIPT_SCHEMA_VERSION

    observed = observe_async_transitions(
        {
            "experiment": {"execution_graph": graph},
            "observations": {
                "process_graph_async_transition_receipts": [receipt]
            },
        }
    )

    assert observed["status"] == "OBSERVED"
    row = observed["evidence"][EVIDENCE_KEY]["transitions"][0]
    assert row["event_scope_mode"] == "correlation_and_idempotency"
    assert row["observed_correlated_row_count"] == 6
    assert row["observed_matching_row_count"] == 3
    assert row["out_of_scope_idempotency_event_count"] == 1
