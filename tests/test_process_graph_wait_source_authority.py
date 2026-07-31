from __future__ import annotations

from ai_test_asset_center.process_graph_wait_contract import (
    STATUS_BLOCKED,
    STATUS_COMPILED,
    WAIT_CONTRACT_INVALID,
    compile_process_graph_wait_contracts,
)


def _graph(*, system_ref: str = "") -> dict:
    return {
        "nodes": [
            {
                "node_id": "source",
                "step_id": "source",
                "operation_ref": "op_source",
                "actor_ref": "actor_1",
                "system_ref": system_ref,
            },
            {
                "node_id": "target",
                "step_id": "target",
                "operation_ref": "op_target",
                "actor_ref": "actor_1",
                "system_ref": system_ref,
            },
        ],
        "edges": [
            {
                "source_node_id": "source",
                "target_node_id": "target",
                "relation_type": "AWAITS",
            }
        ],
        "wait_contracts": [
            {
                "wait_id": "wait_1",
                "source_node_id": "source",
                "target_node_id": "target",
                "observer_operation_ref": "op_wait",
                "actor_ref": "actor_1",
                "system_ref": system_ref,
                "predicate": {
                    "status_codes": [200],
                    "json_path": "$.state",
                    "operator": "equals",
                    "expected_value": "READY",
                },
                "async_policy": {
                    "enabled": True,
                    "expected_max_delay_ms": 100,
                    "poll_interval_ms": 10,
                    "max_attempts": 3,
                    "terminal_condition": "source_declared_predicate",
                },
            }
        ],
    }


def _ir(*, observer_system: str = "") -> dict:
    operations = [
        {"id": "op_source", "method": "GET", "path": "/jobs/{id}"},
        {"id": "op_target", "method": "GET", "path": "/jobs/{id}/result"},
        {
            "id": "op_wait",
            "method": "GET",
            "path": "/jobs/{id}/status",
        },
    ]
    if observer_system:
        operations[-1]["system_ref"] = observer_system
    return {"operations": operations}


def test_wait_contract_cannot_override_declared_method() -> None:
    graph = _graph()
    graph["wait_contracts"][0]["method"] = "HEAD"

    result = compile_process_graph_wait_contracts(graph, behavior_ir=_ir())

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == WAIT_CONTRACT_INVALID
    assert "observer_method_override_forbidden:HEAD!=GET" in result["detail"]


def test_wait_contract_cannot_override_declared_path() -> None:
    graph = _graph()
    graph["wait_contracts"][0]["path"] = "/internal/jobs/{id}/status"

    result = compile_process_graph_wait_contracts(graph, behavior_ir=_ir())

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == WAIT_CONTRACT_INVALID
    assert "observer_path_override_forbidden" in result["detail"]


def test_wait_observer_cannot_cross_target_system() -> None:
    result = compile_process_graph_wait_contracts(
        _graph(system_ref="system_b"),
        behavior_ir=_ir(observer_system="system_a"),
    )

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == WAIT_CONTRACT_INVALID
    assert "observer_operation_system_mismatch:system_a!=system_b" in result[
        "detail"
    ]


def test_secondary_target_requires_observer_system_binding() -> None:
    result = compile_process_graph_wait_contracts(
        _graph(system_ref="system_b"),
        behavior_ir=_ir(observer_system=""),
    )

    assert result["status"] == STATUS_BLOCKED
    assert result["reason_code"] == WAIT_CONTRACT_INVALID
    assert "observer_operation_system_unbound:target=system_b" in result[
        "detail"
    ]


def test_exact_declared_transport_delegates_to_bounded_wait_compiler() -> None:
    graph = _graph(system_ref="system_b")
    result = compile_process_graph_wait_contracts(
        graph,
        behavior_ir=_ir(observer_system="system_b"),
    )

    assert result["status"] == STATUS_COMPILED
    contract = result["graph"]["wait_contracts_by_target"]["target"]
    assert contract["method"] == "GET"
    assert contract["path_template"] == "/jobs/{id}/status"
    assert contract["system_ref"] == "system_b"
