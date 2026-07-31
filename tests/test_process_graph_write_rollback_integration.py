from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import process_graph_write_contract as graph_write


def _graph() -> dict:
    return {
        "execution_graph_id": "graph_write_1",
        "process_id": "process_write_1",
        "nodes": [
            {
                "node_id": "write_a",
                "step_id": "write_a",
                "operation_ref": "op_a",
                "method": "POST",
                "path": "/items/a",
            },
            {
                "node_id": "write_b",
                "step_id": "write_b",
                "operation_ref": "op_b",
                "method": "POST",
                "path": "/items/b",
            },
        ],
        "edges": [
            {"source_node_id": "write_a", "target_node_id": "write_b"}
        ],
        "topological_order": ["write_a", "write_b"],
    }


def _write_contract() -> dict:
    return {
        "schema_version": "qualibug.process-graph-write-contract.v1",
        "contract_id": "write_contract_1",
        "proof_set_id": "proof_set_1",
        "write_step_ids": ["write_a", "write_b"],
        "write_contracts_by_node": {},
        "cleanup_steps": [
            {
                "step_id": "cleanup_b",
                "source_step_id": "write_b",
                "operation_ref": "undo_b",
            },
            {
                "step_id": "cleanup_a",
                "source_step_id": "write_a",
                "operation_ref": "undo_a",
            },
        ],
        "observer_operations_by_node": {},
        "proof_set": {
            "proof_set_id": "proof_set_1",
            "proof_ids_by_step": {},
        },
    }


def test_public_finalizer_binds_one_rollback_fingerprint_everywhere(
    monkeypatch,
) -> None:
    graph = _graph()
    write_contract = _write_contract()
    experiment = {
        "experiment_id": "exp_1",
        "compile_receipt": {"status": "COMPILED"},
        "treatment_plan": [
            {"step_id": "write_a", "operation_ref": "op_a"},
            {"step_id": "write_b", "operation_ref": "op_b"},
        ],
        "cleanup_plan": [],
        "observers": [],
        "safety_contract": {},
    }

    monkeypatch.setattr(
        graph_write._core,
        "_extract_graph",
        lambda exp: (deepcopy(graph), ""),
    )
    monkeypatch.setattr(
        graph_write._core,
        "_canonicalize_graph",
        lambda graph_row, behavior_ir: (
            deepcopy(graph),
            deepcopy(write_contract),
            "",
            "",
        ),
    )
    monkeypatch.setattr(
        graph_write._core,
        "_merge_graph_observers",
        lambda exp, contract: list(exp.get("observers") or []),
    )
    monkeypatch.setattr(
        graph_write,
        "finalize_process_graph_reversibility",
        lambda exp, behavior_ir: exp,
    )

    result = graph_write.finalize_process_graph_write_contract(
        experiment,
        {"operations": []},
    )

    rollback = result["process_graph_rollback_contract"]
    fingerprint = rollback["contract_fingerprint"]
    assert rollback["status"] == "FROZEN"
    assert rollback["cleanup_order"] == ["write_b", "write_a"]
    assert rollback["downstream_write_step_ids_by_source"] == {
        "write_a": ["write_b"],
        "write_b": [],
    }
    assert result["execution_graph"]["rollback_contract_id"] == fingerprint
    assert result["execution_graph"]["rollback_contract"] == rollback
    assert result["process_graph_write_contract"][
        "rollback_contract_id"
    ] == fingerprint
    assert result["process_graph_write_contract"][
        "rollback_contract"
    ] == rollback
    assert result["safety_contract"][
        "process_graph_rollback_contract_id"
    ] == fingerprint
    assert result["compile_receipt"][
        "process_graph_rollback_contract_id"
    ] == fingerprint
    assert result["cleanup_plan"] == write_contract["cleanup_steps"]
    assert all(
        step["_graph_rollback_contract_id"] == fingerprint
        and step["_execution_graph"]["rollback_contract_id"] == fingerprint
        for step in result["treatment_plan"]
    )
