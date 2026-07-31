from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.process_graph_rollback_contract import (
    ROLLBACK_CONTRACT_INVALID,
    STATUS_BLOCKED,
    STATUS_FROZEN,
    freeze_process_graph_rollback_contract,
    validate_process_graph_rollback_contract,
)


def _graph() -> dict:
    return {
        "execution_graph_id": "graph_rollback_1",
        "process_id": "process_rollback_1",
        "nodes": [
            {"node_id": "write_a"},
            {"node_id": "write_b"},
            {"node_id": "write_c"},
            {"node_id": "write_d"},
        ],
        "edges": [
            {"source_node_id": "write_a", "target_node_id": "write_b"},
            {"source_node_id": "write_b", "target_node_id": "write_c"},
        ],
        "topological_order": ["write_a", "write_b", "write_c", "write_d"],
    }


def _write_contract() -> dict:
    return {
        "contract_id": "graph_write_contract_1",
        "write_step_ids": ["write_a", "write_b", "write_c", "write_d"],
        "cleanup_steps": [
            {"step_id": "cleanup_d", "source_step_id": "write_d"},
            {"step_id": "cleanup_c", "source_step_id": "write_c"},
            {"step_id": "cleanup_b", "source_step_id": "write_b"},
            {"step_id": "cleanup_a", "source_step_id": "write_a"},
        ],
    }


def test_rollback_contract_freezes_transitive_and_direct_dependencies() -> None:
    contract = freeze_process_graph_rollback_contract(
        _graph(),
        _write_contract(),
    )

    assert contract["status"] == STATUS_FROZEN
    assert contract["cleanup_order"] == [
        "write_d",
        "write_c",
        "write_b",
        "write_a",
    ]
    assert contract["downstream_write_step_ids_by_source"] == {
        "write_a": ["write_c", "write_b"],
        "write_b": ["write_c"],
        "write_c": [],
        "write_d": [],
    }
    assert contract["direct_downstream_write_step_ids_by_source"] == {
        "write_a": ["write_b"],
        "write_b": ["write_c"],
        "write_c": [],
        "write_d": [],
    }
    assert contract["safe_prerequisite_outcomes"] == [
        "COMPLETED",
        "NOT_REQUIRED",
    ]
    assert contract["contract_fingerprint"]


def test_cleanup_order_must_match_reverse_write_topology() -> None:
    write_contract = _write_contract()
    write_contract["cleanup_steps"] = list(
        reversed(write_contract["cleanup_steps"])
    )

    contract = freeze_process_graph_rollback_contract(
        _graph(),
        write_contract,
    )

    assert contract["status"] == STATUS_BLOCKED
    assert contract["reason_code"] == ROLLBACK_CONTRACT_INVALID
    assert "cleanup_order_not_reverse_write_topology" in contract["detail"]


def test_runtime_validation_rejects_contract_payload_drift() -> None:
    graph = _graph()
    write_contract = _write_contract()
    contract = freeze_process_graph_rollback_contract(graph, write_contract)
    drifted = deepcopy(contract)
    drifted["downstream_write_step_ids_by_source"]["write_a"] = []

    valid, detail = validate_process_graph_rollback_contract(
        graph,
        write_contract,
        drifted,
    )

    assert valid is False
    assert detail in {
        "rollback_contract_fingerprint_mismatch",
        "rollback_contract_payload_mismatch",
    }


def test_independent_branch_has_no_false_dependency_gate() -> None:
    contract = freeze_process_graph_rollback_contract(
        _graph(),
        _write_contract(),
    )

    assert contract["downstream_write_step_ids_by_source"]["write_d"] == []
    assert contract["direct_downstream_write_step_ids_by_source"]["write_d"] == []
