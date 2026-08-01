from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import process_graph_cleanup_executor as cleanup_runtime
from ai_test_asset_center.process_graph_rollback_contract import (
    freeze_process_graph_rollback_contract,
)
from tests.test_process_graph_dependency_rollback import (
    _governed_step,
    _install_fake_core,
    _receipt_source,
)


def _experiment() -> dict:
    graph = {
        "execution_graph_id": "graph_cross_system_fanout",
        "process_id": "cross_system_fanout",
        "nodes": [
            {"node_id": "create_order"},
            {"node_id": "reserve_inventory"},
            {"node_id": "charge_payment"},
        ],
        "edges": [
            {
                "source_node_id": "create_order",
                "target_node_id": "reserve_inventory",
            },
            {
                "source_node_id": "create_order",
                "target_node_id": "charge_payment",
            },
        ],
        "topological_order": [
            "create_order",
            "reserve_inventory",
            "charge_payment",
        ],
    }
    cleanup_steps = [
        {
            "step_id": "cleanup_charge",
            "source_step_id": "charge_payment",
            "operation_ref": "undo_charge",
            "system_ref": "payment",
        },
        {
            "step_id": "cleanup_inventory",
            "source_step_id": "reserve_inventory",
            "operation_ref": "undo_inventory",
            "system_ref": "wms",
        },
        {
            "step_id": "cleanup_order",
            "source_step_id": "create_order",
            "operation_ref": "undo_order",
            "system_ref": "erp",
        },
    ]
    write_contract = {
        "contract_id": "write_contract_fanout",
        "proof_set_id": "proof_set_fanout",
        "write_step_ids": [
            "create_order",
            "reserve_inventory",
            "charge_payment",
        ],
        "cleanup_steps": cleanup_steps,
    }
    rollback = freeze_process_graph_rollback_contract(graph, write_contract)
    write_contract["rollback_contract"] = deepcopy(rollback)
    write_contract["rollback_contract_id"] = rollback["contract_fingerprint"]
    graph["rollback_contract"] = deepcopy(rollback)
    graph["rollback_contract_id"] = rollback["contract_fingerprint"]
    return {
        "execution_graph": graph,
        "process_graph_write_contract": write_contract,
        "process_graph_rollback_contract": rollback,
        "cleanup_plan": cleanup_steps,
        "safety_contract": {
            "cleanup_authority": "process_graph_write_contract"
        },
    }


def test_failed_branch_restores_sibling_but_blocks_common_ancestor(monkeypatch) -> None:
    called: list[str] = []
    _install_fake_core(
        monkeypatch,
        {"charge_payment": "FAILED"},
        called,
    )
    observations = {
        "process_graph_runtime": {
            "node_status": {
                "create_order": "SUCCEEDED",
                "reserve_inventory": "SUCCEEDED",
                "charge_payment": "FAILED",
            }
        }
    }
    result = cleanup_runtime.execute_process_graph_cleanup(
        exp=_experiment(),
        steps_out=[
            _governed_step("create_order"),
            _governed_step("reserve_inventory"),
            _governed_step("charge_payment"),
        ],
        observations=observations,
        contract_evidence_receipts=[],
        request_bodies_for_cleanup={},
        runtime_bindings={},
        cleanup_failures=0,
        actors={},
        tokens={},
        eid="exp_fanout",
        oid="obl_fanout",
        resolved_campaign_id="campaign_fanout",
        resolved_execution_id="run_fanout",
        campaign_id="campaign_fanout",
        root=None,
        project="project_fanout",
        base_url="https://erp.test.example",
        runtime_contract={},
    )

    assert called == ["charge_payment", "reserve_inventory"]
    assert observations["process_graph_rollback_outcomes"] == {
        "charge_payment": "FAILED",
        "reserve_inventory": "COMPLETED",
        "create_order": "BLOCKED",
    }
    receipts = {
        _receipt_source(row): row
        for row in result["process_graph_cleanup_receipts"]
    }
    assert receipts["create_order"]["evidence"]["reason_code"] == (
        cleanup_runtime.GRAPH_CLEANUP_DEPENDENCY_NOT_RESTORED
    )
    assert receipts["create_order"]["evidence"][
        "unsafe_downstream_outcomes"
    ] == {"charge_payment": "FAILED"}
