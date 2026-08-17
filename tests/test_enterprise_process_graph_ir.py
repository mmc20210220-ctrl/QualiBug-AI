"""Governed Business Behavior IR process-graph projection tests."""
from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.process_graph_ir import (
    build_business_process_graphs,
)


def _behavior(behavior_id: str, operation: str, object_ref: str) -> dict:
    return {
        "behavior_id": behavior_id,
        "behavior_family_id": f"family:{operation}:{object_ref}",
        "operation_ref": operation,
        "object_refs": [object_ref],
        "actor_refs": ["operator"],
        "status": "CONFIRMED",
        "formal_business_rule": True,
        "source_refs": [f"fact:{behavior_id}"],
        "evidence": [{"source_id": "src", "quote": f"{object_ref}{operation}"}],
    }


def _binding(behavior_id: str, interface_id: str, system_ref: str = "") -> dict:
    return {
        "binding_id": f"binding:{behavior_id}",
        "behavior_ref": behavior_id,
        "scenario_planning_ready": True,
        "api_operation_bindings": [
            {
                "status": "BOUND",
                "authoritative": True,
                "interface_id": interface_id,
                "system_ref": system_ref,
                "method": "POST",
                "path": f"/api/{interface_id}",
                "evidence": [{"source_id": "openapi", "quote": interface_id}],
            }
        ],
    }


def _atomic_model() -> dict:
    return {
        "operations": [
            {"operation_id": "operation:create", "name": "创建"},
            {"operation_id": "operation:approve", "name": "审批"},
        ],
        "processes": [
            {
                "process_id": "process:order",
                "name": "订单流程",
                "process_type": "LIFECYCLE_UNIQUE_CHAIN",
                "inputs": ["订单"],
                "participants": ["operator"],
                "steps": [
                    {
                        "order": 1,
                        "transition_id": "transition:create",
                        "operation_ref": "operation:create",
                        "from_state": "草稿",
                        "to_state": "待审批",
                    },
                    {
                        "order": 2,
                        "transition_id": "transition:approve",
                        "operation_ref": "operation:approve",
                        "from_state": "待审批",
                        "to_state": "已审批",
                    },
                ],
                "evidence": [{"source_id": "prd", "quote": "创建后审批"}],
                "status": "UNDERSTOOD",
            }
        ],
    }


def test_projects_atomic_process_through_governed_behavior_bindings():
    model = _atomic_model()
    behaviors = [
        _behavior("behavior:create", "创建", "订单"),
        _behavior("behavior:approve", "审批", "订单"),
    ]
    bindings = [
        _binding("behavior:create", "api:create-order"),
        _binding("behavior:approve", "api:approve-order"),
    ]

    graphs, unknowns, gate = build_business_process_graphs(model, behaviors, bindings)

    assert unknowns == []
    assert gate["status"] == "PASS"
    assert len(graphs) == 1
    graph = graphs[0]
    assert graph["status"] == "COMPILED"
    assert [node["operation_ref"] for node in graph["nodes"]] == [
        "api:create-order",
        "api:approve-order",
    ]
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["relation_type"] == "SOURCE_DECLARED_SEQUENCE"
    assert graph["topological_order"] == [node["node_id"] for node in graph["nodes"]]


def test_missing_implementation_binding_stays_partial_and_visible():
    model = _atomic_model()
    behaviors = [
        _behavior("behavior:create", "创建", "订单"),
        _behavior("behavior:approve", "审批", "订单"),
    ]
    bindings = [_binding("behavior:create", "api:create-order")]

    graphs, unknowns, gate = build_business_process_graphs(model, behaviors, bindings)

    assert graphs[0]["status"] == "PARTIAL"
    assert gate["status"] == "PARTIAL_PROCESS_GRAPH_IR"
    assert any(
        row["reason_code"] == "PROCESS_NODE_IMPLEMENTATION_UNRESOLVED"
        for row in unknowns
    )
    unresolved_node = next(
        node for node in graphs[0]["nodes"] if node["business_operation_ref"] == "审批"
    )
    assert unresolved_node["operation_ref"] == ""
    assert unresolved_node["status"] == "PARTIAL"


def test_expands_multi_object_process_and_preserves_wait_contract():
    model = {
        "operations": [
            {"operation_id": "operation:create-order", "name": "创建订单"},
            {"operation_id": "operation:charge", "name": "支付"},
        ],
        "processes": [
            {
                "process_id": "process:order",
                "name": "订单子流程",
                "process_type": "LIFECYCLE_UNIQUE_CHAIN",
                "inputs": ["订单"],
                "participants": ["operator"],
                "steps": [
                    {
                        "transition_id": "transition:create-order",
                        "operation_ref": "operation:create-order",
                    }
                ],
                "evidence": [{"source_id": "prd", "quote": "创建订单"}],
                "status": "UNDERSTOOD",
            },
            {
                "process_id": "process:payment",
                "name": "支付子流程",
                "process_type": "LIFECYCLE_UNIQUE_CHAIN",
                "inputs": ["支付单"],
                "participants": ["operator"],
                "steps": [
                    {
                        "transition_id": "transition:charge",
                        "operation_ref": "operation:charge",
                    }
                ],
                "evidence": [{"source_id": "prd", "quote": "支付"}],
                "status": "UNDERSTOOD",
            },
            {
                "process_id": "process:order-payment",
                "name": "订单支付跨系统流程",
                "process_type": "MULTI_OBJECT_ORCHESTRATION",
                "process_features": ["MULTI_OBJECT", "ASYNC_MESSAGE_JOIN", "CROSS_SYSTEM"],
                "steps": [
                    {"order": 1, "object_ref": "订单", "process_ref": "process:order"},
                    {"order": 2, "object_ref": "支付单", "process_ref": "process:payment"},
                ],
                "object_links": [
                    {
                        "relation_id": "relation:order-payment",
                        "source_object_ref": "订单",
                        "target_object_ref": "支付单",
                        "source_process_ref": "process:order",
                        "target_process_ref": "process:payment",
                        "relation_type": "TRIGGERS",
                    }
                ],
                "waits": [
                    {
                        "wait_id": "wait:payment",
                        "wait_kind": "MESSAGE_WAIT",
                        "awaited_object_ref": "订单",
                        "awaiting_object_ref": "支付单",
                        "source_backed": True,
                    }
                ],
                "evidence": [{"source_id": "prd", "quote": "订单触发支付并等待消息"}],
                "status": "UNDERSTOOD",
            },
        ],
    }
    behaviors = [
        _behavior("behavior:create-order", "创建订单", "订单"),
        _behavior("behavior:charge", "支付", "支付单"),
    ]
    bindings = [
        _binding("behavior:create-order", "erp:create-order", "erp"),
        _binding("behavior:charge", "payment:charge", "payment"),
    ]

    graphs, unknowns, gate = build_business_process_graphs(model, behaviors, bindings)

    assert unknowns == []
    assert gate["status"] == "PASS"
    composite = next(row for row in graphs if row["process_id"] == "process:order-payment")
    assert composite["status"] == "COMPILED"
    assert len(composite["nodes"]) == 2
    assert len(composite["edges"]) == 1
    assert composite["edges"][0]["relation_type"] == "TRIGGERS"
    assert len(composite["wait_contracts"]) == 1
    wait = composite["wait_contracts"][0]
    assert wait["status"] == "BOUND"
    assert len(wait["awaited_node_refs"]) == 1
    assert len(wait["awaiting_node_refs"]) == 1
    assert wait["source_node_id"] == wait["awaited_node_refs"][0]
    assert wait["target_node_id"] == wait["awaiting_node_refs"][0]
    assert {node["system_ref"] for node in composite["nodes"]} == {"erp", "payment"}
