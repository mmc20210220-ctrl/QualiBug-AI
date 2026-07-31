from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import experiment_compiler_obligation as compiler


def _graph() -> dict:
    return {
        "schema_version": "qualibug.process-execution-graph.v1",
        "execution_graph_id": "graph_event_contract_1",
        "process_id": "process_event_contract_1",
        "nodes": [
            {
                "node_id": "submit",
                "step_id": "submit",
                "operation_ref": "op_submit",
            },
            {
                "node_id": "consume",
                "step_id": "consume",
                "operation_ref": "op_consume",
            },
        ],
        "edges": [
            {
                "edge_id": "edge_submit_consume",
                "source_node_id": "submit",
                "target_node_id": "consume",
                "relation_type": "MESSAGE",
            }
        ],
        "topological_order": ["submit", "consume"],
        "start_node_ids": ["submit"],
        "terminal_node_ids": ["consume"],
        "wait_contracts": [],
    }


def test_compiled_experiment_promotes_one_canonical_step_graph() -> None:
    graph = _graph()
    experiment = {
        "compile_receipt": {"status": "COMPILED"},
        "treatment_plan": [
            {
                "step_id": "submit",
                "operation_ref": "op_submit",
                "_execution_graph": deepcopy(graph),
            },
            {
                "step_id": "consume",
                "operation_ref": "op_consume",
                "_execution_graph": deepcopy(graph),
            },
        ],
    }

    result = compiler._persist_compiled_execution_graph(experiment)

    assert result["execution_graph"] == graph
    assert result["compile_receipt"]["execution_graph_id"] == (
        "graph_event_contract_1"
    )
    assert result["compile_receipt"]["process_id"] == (
        "process_event_contract_1"
    )
    assert result["compile_receipt"]["execution_graph_persisted"] is True


def test_graph_identity_conflict_is_not_promoted() -> None:
    graph_a = _graph()
    graph_b = deepcopy(graph_a)
    graph_b["execution_graph_id"] = "graph_event_contract_2"
    experiment = {
        "compile_receipt": {"status": "COMPILED"},
        "treatment_plan": [
            {"step_id": "submit", "_execution_graph": graph_a},
            {"step_id": "consume", "_execution_graph": graph_b},
        ],
    }

    result = compiler._persist_compiled_execution_graph(experiment)

    assert "execution_graph" not in result
    assert result["compile_receipt"] == {"status": "COMPILED"}


def test_ordinary_experiment_remains_unchanged() -> None:
    experiment = {
        "compile_receipt": {"status": "COMPILED"},
        "treatment_plan": [
            {"step_id": "ordinary", "operation_ref": "op_ordinary"}
        ],
    }

    result = compiler._persist_compiled_execution_graph(experiment)

    assert result == experiment
