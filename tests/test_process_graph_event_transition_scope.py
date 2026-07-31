from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.process_graph_event_transition import (
    EVENT_RETRY_LIMIT_EXCEEDED,
)
from ai_test_asset_center.process_graph_wait_contract import (
    EVENT_TRANSITION_INVALID,
    STATUS_BLOCKED,
    STATUS_COMPILED,
    compile_process_graph_wait_contracts,
    execute_process_graph_wait,
)


IR = {
    "operations": [
        {
            "id": "op_source",
            "method": "POST",
            "path": "/jobs",
            "system_ref": "jobs",
        },
        {
            "id": "op_target",
            "method": "GET",
            "path": "/results/{job_id}",
            "system_ref": "results",
        },
        {
            "id": "op_events",
            "method": "GET",
            "path": "/test-observers/callbacks",
            "system_ref": "results",
        },
    ]
}


def _graph() -> dict:
    return {
        "execution_graph_id": "graph_callback_scope",
        "process_id": "process_callback_scope",
        "nodes": [
            {
                "node_id": "submit",
                "operation_ref": "op_source",
                "actor_ref": "actor_1",
                "system_ref": "jobs",
                "method": "POST",
                "path": "/jobs",
            },
            {
                "node_id": "consume",
                "operation_ref": "op_target",
                "actor_ref": "actor_1",
                "system_ref": "results",
                "method": "GET",
                "path": "/results/{job_id}",
            },
        ],
        "edges": [
            {
                "edge_id": "edge_callback",
                "source_node_id": "submit",
                "target_node_id": "consume",
                "relation_type": "TRIGGERS",
            }
        ],
        "topological_order": ["submit", "consume"],
        "wait_contracts": [
            {
                "wait_id": "wait_callback",
                "source_node_id": "submit",
                "target_node_id": "consume",
                "observer_operation_ref": "op_events",
                "actor_ref": "actor_1",
                "system_ref": "results",
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
                    "delivery_kind": "callback",
                    "delivery_semantics": "at_least_once",
                    "events_path": "$.items",
                    "event_id_field": "$.callback_id",
                    "event_type_field": "$.callback_type",
                    "correlation_field": "$.job_id",
                    "correlation_binding": "job_id",
                    "correlation_query_parameter": "job_id",
                    "expected_event_type": "JobCompleted",
                    "expected_min_count": 1,
                    "expected_max_count": 2,
                    "delivery_attempt_field": "$.attempt",
                    "expected_max_delivery_attempt": 2,
                    "source_refs": [
                        {
                            "source_id": "callback_spec",
                            "kind": "formal_event_contract",
                            "locator": "callbacks.JobCompleted",
                        }
                    ],
                },
            }
        ],
    }


def test_event_semantics_require_their_own_source_refs() -> None:
    graph = _graph()
    graph["wait_contracts"][0]["event_transition"]["source_refs"] = []

    result = compile_process_graph_wait_contracts(graph, behavior_ir=IR)

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == EVENT_TRANSITION_INVALID
    assert "event_source_refs_missing" in result["detail"]


def test_duplicate_edge_identity_blocks_event_contract() -> None:
    graph = _graph()
    duplicate = deepcopy(graph["edges"][0])
    duplicate["edge_id"] = "edge_callback_duplicate"
    graph["edges"].append(duplicate)

    result = compile_process_graph_wait_contracts(graph, behavior_ir=IR)

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == EVENT_TRANSITION_INVALID
    assert "event_edge_identity_ambiguous" in result["detail"]


def test_event_contract_fingerprint_binds_edge_and_base_wait() -> None:
    result = compile_process_graph_wait_contracts(_graph(), behavior_ir=IR)

    assert result["status"] == STATUS_COMPILED
    event = result["graph"]["wait_contracts_by_target"]["consume"][
        "event_transition_contract"
    ]
    assert event["edge_id"] == "edge_callback"
    assert event["wait_contract_fingerprint"]
    assert event["source_refs"][0]["source_id"] == "callback_spec"


def test_declared_retry_limit_violation_is_measured() -> None:
    result = compile_process_graph_wait_contracts(_graph(), behavior_ir=IR)
    assert result["status"] == STATUS_COMPILED
    graph = result["graph"]
    responses = iter(
        [
            {
                "status_code": 200,
                "body": {
                    "items": [
                        {
                            "callback_id": "cb-1",
                            "callback_type": "JobCompleted",
                            "job_id": "JOB-1",
                            "attempt": 3,
                        }
                    ]
                },
            },
            {
                "status_code": 200,
                "body": {
                    "items": [
                        {
                            "callback_id": "cb-1",
                            "callback_type": "JobCompleted",
                            "job_id": "JOB-1",
                            "attempt": 3,
                        }
                    ]
                },
            },
        ]
    )
    ticks = iter([0.0, 0.0, 0.001, 0.002])

    receipt = execute_process_graph_wait(
        graph=graph,
        step=next(row for row in graph["nodes"] if row["node_id"] == "consume"),
        context={
            "base_url": "https://results.example.test",
            "bindings": {"job_id": "JOB-1"},
        },
        actors={"actor_1": {"role": "public"}},
        tokens={},
        read_once=lambda: next(responses),
        sleep=lambda _: None,
        monotonic=lambda: next(ticks),
    )

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["semantic_status"] == "VIOLATION"
    assert receipt["reason_code"] == EVENT_RETRY_LIMIT_EXCEEDED
    assert receipt["retry_limit_violation_count"] == 1
