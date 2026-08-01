from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.process_graph_runtime import (
    GRAPH_RUNTIME_INVALID,
    prepare_graph_runtime,
)


def _write_contract(node_id: str, operation_ref: str) -> dict:
    return {
        "source_step_id": node_id,
        "operation_ref": operation_ref,
        "cleanup_step_id": f"cleanup_{node_id}",
    }


def _graph() -> dict:
    rows = [
        ("create_order", "op_create_order", "POST", "erp", "/orders"),
        (
            "reserve_inventory",
            "op_reserve_inventory",
            "POST",
            "wms",
            "/reservations",
        ),
        (
            "charge_payment",
            "op_charge_payment",
            "POST",
            "payment",
            "/charges",
        ),
        (
            "confirm_order",
            "op_confirm_order",
            "GET",
            "erp",
            "/orders/{order_id}",
        ),
    ]
    nodes = []
    write_contracts = {}
    for node_id, operation_ref, method, system_ref, path in rows:
        node = {
            "node_id": node_id,
            "step_id": node_id,
            "operation_ref": operation_ref,
            "actor_ref": "actor_writer",
            "system_ref": system_ref,
            "method": method,
            "path": path,
        }
        if method != "GET":
            contract = _write_contract(node_id, operation_ref)
            node["write_contract"] = deepcopy(contract)
            node["effect_observer_operations"] = [
                {"operation_ref": f"observe_{node_id}"}
            ]
            write_contracts[node_id] = contract
        nodes.append(node)
    return {
        "execution_graph_id": "graph_write_join",
        "process_id": "process_write_join",
        "nodes": nodes,
        "edges": [
            {
                "edge_id": "edge_order_inventory",
                "source_node_id": "create_order",
                "target_node_id": "reserve_inventory",
                "relation_type": "DEPENDS_ON",
            },
            {
                "edge_id": "edge_order_payment",
                "source_node_id": "create_order",
                "target_node_id": "charge_payment",
                "relation_type": "DEPENDS_ON",
            },
            {
                "edge_id": "edge_inventory_confirm",
                "source_node_id": "reserve_inventory",
                "target_node_id": "confirm_order",
                "relation_type": "DEPENDS_ON",
            },
            {
                "edge_id": "edge_payment_confirm",
                "source_node_id": "charge_payment",
                "target_node_id": "confirm_order",
                "relation_type": "DEPENDS_ON",
            },
        ],
        "topological_order": [
            "create_order",
            "reserve_inventory",
            "charge_payment",
            "confirm_order",
        ],
        "join_groups": [
            {
                "join_node_id": "confirm_order",
                "predecessor_node_ids": [
                    "reserve_inventory",
                    "charge_payment",
                ],
                "status": "BOUND",
            }
        ],
        "wait_contracts": [],
        "wait_contracts_by_target": {},
        "wait_runtime_contract": {
            "schema_version": "qualibug.process-graph-wait-runtime.v1",
            "contract_count": 0,
            "contract_fingerprints": [],
        },
        "write_contracts_by_node": write_contracts,
    }


def _ops() -> dict[str, dict]:
    return {
        node["operation_ref"]: {
            "id": node["operation_ref"],
            "method": node["method"],
            "path": node["path"],
            "system_ref": node["system_ref"],
        }
        for node in _graph()["nodes"]
    }


def _plan(graph: dict) -> list[dict]:
    return [
        {
            "step_id": node["node_id"],
            "operation_ref": node["operation_ref"],
            "actor_ref": node["actor_ref"],
            "system_ref": node["system_ref"],
        }
        for node in graph["nodes"]
    ]


def _runtime_contract() -> dict:
    return {
        "status": "approved",
        "system_ref": "erp",
        "requested_base_url": "https://erp.test.example",
        "approved_base_url": "https://erp.test.example",
        "environment_type": "test",
        "environment_ref": "erp-test",
        "execution_mode": "approved_sandbox_write",
        "approved_targets": {
            "wms": {
                "status": "approved",
                "system_ref": "wms",
                "requested_base_url": "https://wms.test.example",
                "approved_base_url": "https://wms.test.example",
                "environment_type": "test",
                "environment_ref": "wms-test",
                "execution_mode": "approved_sandbox_write",
                "actor_token_keys": {
                    "actor_writer": "wms:actor_writer",
                },
            },
            "payment": {
                "status": "approved",
                "system_ref": "payment",
                "requested_base_url": "https://payment.test.example",
                "approved_base_url": "https://payment.test.example",
                "environment_type": "test",
                "environment_ref": "payment-test",
                "execution_mode": "approved_sandbox_write",
                "actor_token_keys": {
                    "actor_writer": "payment:actor_writer",
                },
            },
        },
    }


def _prepare(graph: dict) -> dict:
    return prepare_graph_runtime(
        graph=graph,
        treatment_plan=_plan(graph),
        ops=_ops(),
        base_url="https://erp.test.example",
        runtime_contract=_runtime_contract(),
    )


def test_governed_write_graph_accepts_exact_and_join_scope() -> None:
    result = _prepare(_graph())

    assert result["status"] == "READY", result
    assert result["predecessors"]["confirm_order"] == [
        "reserve_inventory",
        "charge_payment",
    ]


def test_governed_write_graph_rejects_partial_join_scope_before_transport() -> None:
    graph = _graph()
    graph["join_groups"][0]["predecessor_node_ids"] = [
        "reserve_inventory"
    ]

    result = _prepare(graph)

    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == GRAPH_RUNTIME_INVALID
    assert result["detail"].startswith(
        "join_1_predecessor_identity_invalid:confirm_order"
    )
