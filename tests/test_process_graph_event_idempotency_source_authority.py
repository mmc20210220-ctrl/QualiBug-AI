from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import process_graph_event_transition as event_runtime
from ai_test_asset_center import process_graph_wait_contract_core as wait_core
from ai_test_asset_center.process_graph_async_transition_observer import (
    EVIDENCE_KEY,
    observe_async_transitions,
)
from ai_test_asset_center.process_graph_wait_contract import (
    EVENT_TRANSITION_INVALID,
    STATUS_BLOCKED,
    STATUS_COMPILED,
    STATUS_CONVERGED,
    compile_process_graph_wait_contracts,
    compiled_wait_runtime_ready,
    execute_process_graph_wait,
)


def _ir(request_value: str | None = "<request_id>") -> dict:
    source = {
        "id": "op_submit",
        "method": "POST",
        "path": "/orders",
        "system_ref": "orders",
    }
    if request_value is not None:
        source["request_example"] = {"idempotency_key": request_value}
    return {
        "operations": [
            source,
            {
                "id": "op_consume",
                "method": "GET",
                "path": "/notifications/{order_id}",
                "system_ref": "notifications",
            },
            {
                "id": "op_events",
                "method": "GET",
                "path": "/test-observers/events",
                "system_ref": "notifications",
            },
        ]
    }


def _graph() -> dict:
    return {
        "execution_graph_id": "graph_event_idempotency_authority",
        "process_id": "process_event_idempotency_authority",
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
                "edge_id": "edge_order_event",
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
                "wait_id": "wait_order_event",
                "source_node_id": "submit_order",
                "target_node_id": "consume_notification",
                "observer_operation_ref": "op_events",
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
                    "correlation_field": "$.aggregate_id",
                    "correlation_binding": "order_id",
                    "correlation_query_parameter": "aggregate_id",
                    "expected_event_type": "OrderCreated",
                    "expected_min_count": 1,
                    "expected_max_count": 1,
                    "idempotency_key_binding": "request_id",
                    "idempotency_key_field": "$.idempotency_key",
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


def _compile(ir: dict | None = None) -> dict:
    result = compile_process_graph_wait_contracts(
        _graph(),
        behavior_ir=ir or _ir(),
    )
    assert result["status"] == STATUS_COMPILED, result
    return result["graph"]


def _event(event_id: str, request_id: str) -> dict:
    return {
        "event_id": event_id,
        "event_type": "OrderCreated",
        "aggregate_id": "ORD-42",
        "idempotency_key": request_id,
    }


def _execute(graph: dict, rows: list[dict]) -> dict:
    response = {"status_code": 200, "body": {"items": rows}}
    responses = iter([response, response])
    ticks = iter([0.0, 0.0, 0.001, 0.002])
    target = next(
        row
        for row in graph["nodes"]
        if row["node_id"] == "consume_notification"
    )
    return execute_process_graph_wait(
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


def test_compile_freezes_source_request_idempotency_authority() -> None:
    graph = _compile()
    event = graph["wait_contracts_by_target"]["consume_notification"][
        "event_transition_contract"
    ]
    proof = event["idempotency_binding_contract"]

    assert proof["source_node_id"] == "submit_order"
    assert proof["source_operation_ref"] == "op_submit"
    assert proof["binding_target"] == "request_id"
    assert proof["request_locations"] == [
        "request_example:$.idempotency_key"
    ]
    assert proof["source_request_contract_fingerprint"]
    assert compiled_wait_runtime_ready(graph) == (True, "")


def test_global_binding_without_source_request_use_cannot_scope_event() -> None:
    result = compile_process_graph_wait_contracts(
        _graph(),
        behavior_ir=_ir(request_value=None),
    )

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == EVENT_TRANSITION_INVALID
    assert (
        "event_idempotency_source_request_unresolved:request_id"
        in result["detail"]
    )


def test_static_or_non_runtime_request_markers_are_not_authority() -> None:
    for value in ("REQ-STATIC", "{{request_id}}"):
        result = compile_process_graph_wait_contracts(
            _graph(),
            behavior_ir=_ir(request_value=value),
        )
        assert result["status"] == STATUS_BLOCKED
        assert (
            "event_idempotency_source_request_unresolved:request_id"
            in result["detail"]
        )


def test_idempotency_source_proof_drift_blocks_before_transport() -> None:
    graph = _compile()
    wait = graph["wait_contracts"][0]
    event = wait["event_transition_contract"]
    event["idempotency_binding_contract"]["source_operation_ref"] = "op_other"
    event.pop("contract_fingerprint", None)
    event["contract_fingerprint"] = event_runtime._fingerprint(event)
    wait.pop("contract_fingerprint", None)
    wait["contract_fingerprint"] = wait_core._fingerprint(wait)
    graph["wait_contracts_by_target"]["consume_notification"] = deepcopy(wait)
    graph["wait_runtime_contract"]["contract_fingerprints"] = [
        wait["contract_fingerprint"]
    ]
    graph["wait_runtime_contract"]["event_transition_fingerprints"] = [
        event["contract_fingerprint"]
    ]

    assert compiled_wait_runtime_ready(graph) == (
        False,
        "event_idempotency_binding_contract_drift",
    )


def test_delayed_foreign_event_is_excluded_by_proven_source_request_scope() -> None:
    graph = _compile()
    receipt = _execute(
        graph,
        [
            _event("evt-old", "REQ-OLD"),
            _event("evt-current", "REQ-1"),
        ],
    )

    assert receipt["status"] == STATUS_CONVERGED
    assert receipt["semantic_status"] == "PASS"
    assert receipt["observed_unique_event_count"] == 1
    assert receipt["out_of_scope_idempotency_event_count"] == 1
    assert receipt["idempotency_scope_authority"] == (
        "source_request_binding_contract"
    )
    assert receipt["idempotency_binding_contract_fingerprint"]
    assert receipt["source_request_contract_fingerprint"]

    observed = observe_async_transitions(
        {
            "experiment": {"execution_graph": graph},
            "observations": {
                "process_graph_async_transition_receipts": [receipt]
            },
        }
    )
    transition = observed["evidence"][EVIDENCE_KEY]["transitions"][0]
    assert transition["idempotency_scope_authority"] == (
        "source_request_binding_contract"
    )
    assert transition["idempotency_binding_contract_fingerprint"]
    assert transition["source_request_contract_fingerprint"]
