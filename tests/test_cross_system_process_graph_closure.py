from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding import (
    build_behavior_implementation_bindings,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.process_graph_ir import (
    build_business_process_graphs,
)
from ai_test_asset_center.process_graph_runtime import (
    graph_step_context,
    prepare_graph_runtime,
    record_graph_step_outcome,
)


def _behavior(behavior_id: str, operation: str, object_ref: str, source_ref: str) -> dict:
    return {
        "behavior_id": behavior_id,
        "behavior_family_id": f"family:{operation}:{object_ref}",
        "source_refs": [source_ref],
        "actor_refs": ["operator"],
        "operation_ref": operation,
        "object_refs": [object_ref],
        "preconditions": [],
        "state_effects": [],
        "data_effects": [],
        "permission_decision": "UNSPECIFIED",
        "condition_combinator": "NO_CONDITION",
        "status": "CONFIRMED",
        "formal_business_rule": True,
        "evidence": [{"source_id": "prd", "quote": f"{object_ref}{operation}"}],
    }


def _asset() -> dict:
    return {
        "interfaces": [
            {
                "interface_id": "erp:get-order-context",
                "source_id": "erp-openapi",
                "method": "GET",
                "path": "/orders/current",
                "operation_id": "获取订单上下文",
                "summary": "获取订单上下文",
                "system_ref": "erp",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "order_id",
                        "json_path": "$.data.orderId",
                    }
                ],
            },
            {
                "interface_id": "payment:get-payment",
                "source_id": "payment-openapi",
                "method": "GET",
                "path": "/payments/{orderId}",
                "operation_id": "获取支付记录",
                "summary": "获取支付记录",
                "service_ref": "payment",
                "input_binding_refs": [
                    {
                        "producer_output_field": "order_id",
                        "target": "orderId",
                    }
                ],
            },
        ],
        "relationships": [
            {
                "from": "fact:get-order",
                "to": "erp:get-order-context",
                "relation": "behavior_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
            },
            {
                "from": "fact:get-payment",
                "to": "payment:get-payment",
                "relation": "behavior_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
            },
        ],
        "rule_library": [],
        "data_tables": [],
        "field_dictionary": [],
        "ui_design_specs": [],
    }


def _model() -> dict:
    return {
        "operations": [
            {"operation_id": "operation:get-order", "name": "获取订单上下文"},
            {"operation_id": "operation:get-payment", "name": "获取支付记录"},
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
                        "transition_id": "transition:get-order",
                        "operation_ref": "operation:get-order",
                    }
                ],
                "evidence": [{"source_id": "prd", "quote": "读取订单"}],
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
                        "transition_id": "transition:get-payment",
                        "operation_ref": "operation:get-payment",
                    }
                ],
                "evidence": [{"source_id": "prd", "quote": "读取支付"}],
                "status": "UNDERSTOOD",
            },
            {
                "process_id": "process:order-payment",
                "name": "订单支付跨系统读取",
                "process_type": "MULTI_OBJECT_ORCHESTRATION",
                "process_features": ["MULTI_OBJECT", "CROSS_SYSTEM"],
                "steps": [
                    {"object_ref": "订单", "process_ref": "process:order"},
                    {"object_ref": "支付单", "process_ref": "process:payment"},
                ],
                "object_links": [
                    {
                        "relation_id": "relation:order-payment",
                        "source_object_ref": "订单",
                        "target_object_ref": "支付单",
                        "source_process_ref": "process:order",
                        "target_process_ref": "process:payment",
                        "source_system_ref": "erp",
                        "target_system_ref": "payment",
                        "relation_type": "DEPENDS_ON",
                        "binding_refs": [
                            {
                                "canonical_field_id": "order_id",
                                "producer_response_path": "$.data.orderId",
                                "consumer_target": "orderId",
                            }
                        ],
                        "evidence": [{"source_id": "prd", "quote": "支付按订单ID查询"}],
                    }
                ],
                "waits": [],
                "evidence": [{"source_id": "prd", "quote": "ERP订单关联支付系统"}],
                "status": "UNDERSTOOD",
            },
        ],
    }


def _bindings(asset: dict | None = None) -> tuple[list[dict], list[dict]]:
    behaviors = [
        _behavior("behavior:get-order", "获取订单上下文", "订单", "fact:get-order"),
        _behavior("behavior:get-payment", "获取支付记录", "支付单", "fact:get-payment"),
    ]
    bindings, unknowns, conflicts, _gate = build_behavior_implementation_bindings(
        asset or _asset(), behaviors
    )
    assert conflicts == []
    assert all(binding["api_operation_bindings"] for binding in bindings)
    return behaviors, bindings


def _fan_in_asset() -> dict:
    return {
        "interfaces": [
            {
                "interface_id": "erp:get-order",
                "source_id": "erp-openapi",
                "method": "GET",
                "path": "/orders/current",
                "operation_id": "读取订单",
                "summary": "读取订单",
                "system_ref": "erp",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "order_id",
                        "json_path": "$.data.orderId",
                    }
                ],
            },
            {
                "interface_id": "inventory:get-stock",
                "source_id": "inventory-openapi",
                "method": "GET",
                "path": "/stock/current",
                "operation_id": "读取库存检查",
                "summary": "读取库存检查",
                "system_ref": "inventory",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "stock_check_id",
                        "json_path": "$.data.stockCheckId",
                    }
                ],
            },
            {
                "interface_id": "procurement:get-readiness",
                "source_id": "procurement-openapi",
                "method": "GET",
                "path": "/purchase/readiness",
                "operation_id": "读取采购就绪状态",
                "summary": "读取采购就绪状态",
                "system_ref": "procurement",
                "input_binding_refs": [
                    {
                        "producer_output_field": "order_id",
                        "target": "orderId",
                    },
                    {
                        "producer_output_field": "stock_check_id",
                        "target": "stockCheckId",
                    },
                ],
            },
        ],
        "relationships": [
            {
                "from": "fact:read-order",
                "to": "erp:get-order",
                "relation": "behavior_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
            },
            {
                "from": "fact:read-stock",
                "to": "inventory:get-stock",
                "relation": "behavior_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
            },
            {
                "from": "fact:read-purchase",
                "to": "procurement:get-readiness",
                "relation": "behavior_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
            },
        ],
        "rule_library": [],
        "data_tables": [],
        "field_dictionary": [],
        "ui_design_specs": [],
    }


def _fan_in_model() -> dict:
    children = [
        ("process:order", "订单", "读取订单"),
        ("process:stock", "库存检查", "读取库存检查"),
        ("process:purchase", "采购订单", "读取采购就绪状态"),
    ]
    processes = [
        {
            "process_id": process_id,
            "name": f"{object_ref}子流程",
            "process_type": "LIFECYCLE_UNIQUE_CHAIN",
            "inputs": [object_ref],
            "participants": ["operator"],
            "steps": [
                {
                    "transition_id": f"transition:{process_id}",
                    "operation_ref": operation,
                }
            ],
            "evidence": [{"source_id": "prd", "quote": object_ref}],
            "status": "UNDERSTOOD",
        }
        for process_id, object_ref, operation in children
    ]
    processes.append(
        {
            "process_id": "process:purchase-fan-in",
            "name": "订单与库存检查汇合采购",
            "process_type": "MULTI_OBJECT_ORCHESTRATION",
            "process_features": [
                "MULTI_OBJECT",
                "ORCHESTRATION",
                "EXPLICIT_JOIN",
                "CROSS_SYSTEM",
            ],
            "trigger": {"object_refs": ["订单", "库存检查"]},
            "entry_mode": "SOURCE_DECLARED_MULTI_START_JOIN",
            "inputs": ["订单", "库存检查"],
            "steps": [
                {"object_ref": "订单", "process_ref": "process:order"},
                {"object_ref": "库存检查", "process_ref": "process:stock"},
                {"object_ref": "采购订单", "process_ref": "process:purchase"},
            ],
            "object_links": [
                {
                    "relation_id": "relation:order-purchase",
                    "source_object_ref": "订单",
                    "target_object_ref": "采购订单",
                    "source_process_ref": "process:order",
                    "target_process_ref": "process:purchase",
                    "source_system_ref": "erp",
                    "target_system_ref": "procurement",
                    "relation_type": "GENERATES",
                    "orchestration_markers": ["EXPLICIT_JOIN", "CROSS_SYSTEM"],
                    "binding_refs": [
                        {
                            "canonical_field_id": "order_id",
                            "producer_response_path": "$.data.orderId",
                            "consumer_target": "orderId",
                        }
                    ],
                    "source_refs": ["prd#join-order"],
                    "evidence": [{"source_id": "prd", "quote": "订单完成后参与采购汇合"}],
                },
                {
                    "relation_id": "relation:stock-purchase",
                    "source_object_ref": "库存检查",
                    "target_object_ref": "采购订单",
                    "source_process_ref": "process:stock",
                    "target_process_ref": "process:purchase",
                    "source_system_ref": "inventory",
                    "target_system_ref": "procurement",
                    "relation_type": "GENERATES",
                    "orchestration_markers": ["EXPLICIT_JOIN", "CROSS_SYSTEM"],
                    "binding_refs": [
                        {
                            "canonical_field_id": "stock_check_id",
                            "producer_response_path": "$.data.stockCheckId",
                            "consumer_target": "stockCheckId",
                        }
                    ],
                    "source_refs": ["prd#join-stock"],
                    "evidence": [{"source_id": "prd", "quote": "库存检查完成后参与采购汇合"}],
                },
            ],
            "joins": [
                {
                    "join_id": "join:purchase",
                    "join_kind": "SOURCE_EXPLICIT_JOIN",
                    "target_object_ref": "采购订单",
                    "incoming_object_refs": ["订单", "库存检查"],
                    "source_refs": ["prd#join-order", "prd#join-stock"],
                    "evidence": [{"source_id": "prd", "quote": "订单和库存检查均完成后"}],
                }
            ],
            "waits": [],
            "evidence": [{"source_id": "prd", "quote": "订单和库存检查均完成后进入采购"}],
            "status": "UNDERSTOOD",
        }
    )
    return {"operations": [], "processes": processes}


def _fan_in_bindings() -> tuple[list[dict], list[dict]]:
    behaviors = [
        _behavior("behavior:order", "读取订单", "订单", "fact:read-order"),
        _behavior("behavior:stock", "读取库存检查", "库存检查", "fact:read-stock"),
        _behavior(
            "behavior:purchase",
            "读取采购就绪状态",
            "采购订单",
            "fact:read-purchase",
        ),
    ]
    bindings, unknowns, conflicts, _gate = build_behavior_implementation_bindings(
        _fan_in_asset(), behaviors
    )
    assert all(
        row.get("reason_code") == "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED"
        for row in unknowns
    )
    assert conflicts == []
    return behaviors, bindings


def test_source_declared_system_and_handoff_contracts_reach_process_graph() -> None:
    behaviors, bindings = _bindings()

    graphs, unknowns, gate = build_business_process_graphs(
        _model(), behaviors, bindings
    )

    assert unknowns == []
    assert gate["status"] == "PASS"
    graph = next(row for row in graphs if row["process_id"] == "process:order-payment")
    assert graph["status"] == "COMPILED"
    assert {row["system_ref"] for row in graph["nodes"]} == {"erp", "payment"}
    edge = graph["edges"][0]
    assert edge["source_system_ref"] == "erp"
    assert edge["target_system_ref"] == "payment"
    assert len(edge["binding_refs"]) == 1
    source = next(row for row in graph["nodes"] if row["system_ref"] == "erp")
    target = next(row for row in graph["nodes"] if row["system_ref"] == "payment")
    assert source["output_binding_specs"] == [
        {"canonical_field_id": "order_id", "json_path": "$.data.orderId"}
    ]
    assert target["input_binding_refs"][0]["producer_node_id"] == source["node_id"]
    assert target["input_binding_refs"][0]["producer_output_field"] == "order_id"
    assert target["input_binding_refs"][0]["target"] == "orderId"


def test_cross_system_graph_blocks_when_interface_system_identity_is_missing() -> None:
    asset = _asset()
    asset["interfaces"][1].pop("service_ref")
    behaviors, bindings = _bindings(asset)

    graphs, unknowns, gate = build_business_process_graphs(
        _model(), behaviors, bindings
    )

    graph = next(row for row in graphs if row["process_id"] == "process:order-payment")
    assert graph["status"] == "PARTIAL"
    assert gate["status"] == "PARTIAL_PROCESS_GRAPH_IR"
    assert any(
        row["reason_code"] == "PROCESS_GRAPH_CROSS_SYSTEM_TARGET_UNRESOLVED"
        for row in unknowns
    )


def test_cross_system_graph_blocks_relation_and_interface_system_mismatch() -> None:
    model = _model()
    model["processes"][-1]["object_links"][0]["target_system_ref"] = "warehouse"
    behaviors, bindings = _bindings()

    graphs, unknowns, gate = build_business_process_graphs(model, behaviors, bindings)

    graph = next(row for row in graphs if row["process_id"] == "process:order-payment")
    assert graph["status"] == "PARTIAL"
    assert gate["status"] == "PARTIAL_PROCESS_GRAPH_IR"
    assert any(
        row["reason_code"] == "PROCESS_EDGE_SYSTEM_SCOPE_MISMATCH"
        for row in unknowns
    )


def test_cross_system_handoff_executes_through_existing_runtime_authority() -> None:
    behaviors, bindings = _bindings()
    graphs, unknowns, _gate = build_business_process_graphs(
        _model(), behaviors, bindings
    )
    assert unknowns == []
    graph = next(row for row in graphs if row["process_id"] == "process:order-payment")
    plan = [
        {
            "step_id": node["node_id"],
            "operation_ref": node["operation_ref"],
            "actor_ref": node["actor_ref"],
            "system_ref": node["system_ref"],
            "_execution_graph": graph,
        }
        for node in graph["nodes"]
    ]
    runtime = prepare_graph_runtime(
        graph=graph,
        treatment_plan=plan,
        ops={
            "erp:get-order-context": {
                "id": "erp:get-order-context",
                "method": "GET",
                "path": "/orders/current",
            },
            "payment:get-payment": {
                "id": "payment:get-payment",
                "method": "GET",
                "path": "/payments/{orderId}",
            },
        },
        base_url="https://erp.example.test",
        runtime_contract={
            "status": "approved",
            "requested_base_url": "https://erp.example.test",
            "approved_base_url": "https://erp.example.test",
            "environment_type": "test",
            "environment_ref": "erp-test",
            "execution_mode": "safe_read_only",
            "system_ref": "erp",
            "approved_targets": {
                "payment": {
                    "status": "approved",
                    "requested_base_url": "https://payment.example.test",
                    "approved_base_url": "https://payment.example.test",
                    "environment_type": "test",
                    "environment_ref": "payment-test",
                    "execution_mode": "safe_read_only",
                    "actor_token_keys": {"operator": "payment:operator"},
                }
            },
        },
    )
    assert runtime["status"] == "READY", runtime
    source_step = next(row for row in plan if row["system_ref"] == "erp")
    target_step = next(row for row in plan if row["system_ref"] == "payment")

    outcome = record_graph_step_outcome(
        runtime=runtime,
        graph=graph,
        step=source_step,
        observation={"status_code": 200, "body": {"data": {"orderId": "ORD-42"}}},
    )
    assert outcome["status"] == "SUCCEEDED"
    context = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=target_step,
        initial_bindings={},
    )
    assert context["status"] == "READY"
    assert context["base_url"] == "https://payment.example.test"
    assert context["bindings"] == {"orderId": "ORD-42"}


def test_object_graph_preserves_source_declared_cross_system_handoff() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.object_graph import (
        build_object_graph,
    )

    fact = {
        "fact_id": "fact:order-payment-handoff",
        "kind": "RULE",
        "status": "ACCEPTED",
        "critical": True,
        "raw_statement": "订单跨系统通知支付单，支付单根据订单ID查询支付记录",
        "subject": {"entity_refs": ["订单", "支付单"]},
        "object": {"entity_refs": ["订单", "支付单"]},
        "conditions": ["跨系统通知"],
        "source_system_ref": "erp",
        "target_system_ref": "payment",
        "binding_refs": [
            {
                "canonical_field_id": "order_id",
                "producer_response_path": "$.data.orderId",
                "consumer_target": "orderId",
            }
        ],
        "source_spans": [
            {
                "source_id": "prd",
                "locator": "prd#order-payment",
                "quote": "订单跨系统通知支付单",
            }
        ],
    }

    relations, unknowns = build_object_graph(
        {"entity_relations": []},
        [fact],
        ["订单", "支付单"],
    )

    assert unknowns == []
    assert len(relations) == 1
    relation = relations[0]
    assert relation["source_system_ref"] == "erp"
    assert relation["target_system_ref"] == "payment"
    assert relation["binding_refs"] == fact["binding_refs"]
    assert "CROSS_SYSTEM" in relation["orchestration_markers"]


def test_object_graph_blocks_conflicting_system_scope_for_same_relation() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.object_graph import (
        build_object_graph,
    )

    rows = []
    for target_system in ("payment", "warehouse"):
        rows.append(
            {
                "edge_id": f"edge:{target_system}",
                "from_entity": "订单",
                "to_entity": "支付单",
                "relation": "depends_on",
                "status": "accepted",
                "derivation": "source_declared_business_relation",
                "source_id": "prd",
                "source_system_ref": "erp",
                "target_system_ref": target_system,
                "binding_refs": [
                    {
                        "canonical_field_id": "order_id",
                        "producer_response_path": "$.data.orderId",
                        "consumer_target": "orderId",
                    }
                ],
            }
        )

    relations, unknowns = build_object_graph(
        {"entity_relations": rows},
        [],
        ["订单", "支付单"],
    )

    assert len(relations) == 1
    assert relations[0]["source_system_ref"] == "erp"
    assert relations[0]["target_system_ref"] == ""
    assert relations[0]["target_system_ref_candidates"] == [
        "payment",
        "warehouse",
    ]
    assert any(
        row["reason_code"] == "OBJECT_RELATION_SYSTEM_SCOPE_CONFLICT"
        for row in unknowns
    )


def test_cross_system_graph_blocks_incomplete_source_declared_handoff() -> None:
    asset = _asset()
    asset["interfaces"][0]["output_binding_specs"] = []
    behaviors, bindings = _bindings(asset)
    model = _model()
    model["processes"][-1]["object_links"][0]["binding_refs"][0].pop(
        "producer_response_path"
    )

    graphs, unknowns, gate = build_business_process_graphs(
        model,
        behaviors,
        bindings,
    )

    graph = next(
        row for row in graphs if row["process_id"] == "process:order-payment"
    )
    assert graph["status"] == "PARTIAL"
    assert gate["status"] == "PARTIAL_PROCESS_GRAPH_IR"
    assert any(
        row["reason_code"] == "PROCESS_EDGE_DATA_HANDOFF_INCOMPLETE"
        for row in unknowns
    )


def test_cross_system_graph_blocks_conflicting_handoff_producer() -> None:
    asset = _asset()
    asset["interfaces"][1]["input_binding_refs"][0][
        "producer_node_id"
    ] = "some-other-producer"
    behaviors, bindings = _bindings(asset)

    graphs, unknowns, gate = build_business_process_graphs(
        _model(),
        behaviors,
        bindings,
    )

    graph = next(
        row for row in graphs if row["process_id"] == "process:order-payment"
    )
    assert graph["status"] == "PARTIAL"
    assert gate["status"] == "PARTIAL_PROCESS_GRAPH_IR"
    assert any(
        row["reason_code"] == "PROCESS_EDGE_INPUT_BINDING_CONFLICT"
        for row in unknowns
    )


def test_cross_system_graph_blocks_conflicting_handoff_output_path() -> None:
    model = _model()
    model["processes"][-1]["object_links"][0]["binding_refs"][0][
        "producer_response_path"
    ] = "$.data.conflictingOrderId"
    behaviors, bindings = _bindings()

    graphs, unknowns, gate = build_business_process_graphs(
        model,
        behaviors,
        bindings,
    )

    graph = next(
        row for row in graphs if row["process_id"] == "process:order-payment"
    )
    assert graph["status"] == "PARTIAL"
    assert gate["status"] == "PARTIAL_PROCESS_GRAPH_IR"
    assert any(
        row["reason_code"] == "PROCESS_EDGE_OUTPUT_BINDING_CONFLICT"
        for row in unknowns
    )


def test_source_object_relation_projects_system_and_handoff_into_composite_process() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder_legacy_v1 import (
        _project_multi_object_processes,
    )
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.object_graph import (
        build_object_graph,
    )

    fact = {
        "fact_id": "fact:order-payment-orchestration",
        "kind": "RULE",
        "status": "ACCEPTED",
        "critical": True,
        "raw_statement": "订单跨系统通知支付单",
        "subject": {"entity_refs": ["订单", "支付单"]},
        "object": {"entity_refs": ["订单", "支付单"]},
        "conditions": ["跨系统通知"],
        "source_system_ref": "erp",
        "target_system_ref": "payment",
        "binding_refs": [
            {
                "canonical_field_id": "order_id",
                "producer_response_path": "$.data.orderId",
                "consumer_target": "orderId",
            }
        ],
        "source_spans": [
            {
                "source_id": "prd",
                "locator": "prd#orchestration",
                "quote": "订单跨系统通知支付单",
            }
        ],
    }
    relations, relation_unknowns = build_object_graph(
        {"entity_relations": []},
        [fact],
        ["订单", "支付单"],
    )
    processes, process_unknowns = _project_multi_object_processes(
        _model()["processes"][:2],
        relations,
    )

    assert relation_unknowns == []
    assert process_unknowns == []
    assert len(processes) == 1
    process = processes[0]
    assert process["process_type"] == "MULTI_OBJECT_ORCHESTRATION"
    assert "CROSS_SYSTEM" in process["process_features"]
    link = process["object_links"][0]
    assert link["source_system_ref"] == "erp"
    assert link["target_system_ref"] == "payment"
    assert link["binding_refs"] == fact["binding_refs"]
    wait = process["waits"][0]
    assert wait["source_system_ref"] == "erp"
    assert wait["target_system_ref"] == "payment"
    assert wait["binding_refs"] == fact["binding_refs"]


def test_compiled_cross_system_graph_enters_existing_multi_step_protocol() -> None:
    from ai_test_asset_center.multi_step_protocol import (
        compile_multi_step_process_protocol,
    )

    behaviors, bindings = _bindings()
    graphs, unknowns, _gate = build_business_process_graphs(
        _model(),
        behaviors,
        bindings,
    )
    assert unknowns == []
    graph = next(
        row for row in graphs if row["process_id"] == "process:order-payment"
    )
    result = compile_multi_step_process_protocol(
        {
            "risk_family": "process",
            "operation_ref": graph["nodes"][0]["operation_ref"],
            "treatment_actor_ref": "operator",
            "property_spec": {
                "process_graph": graph,
                "source_refs": [
                    {"source_id": "prd", "locator": "prd#order-payment"}
                ],
            },
            "behavior_ir": {
                "operations": [
                    {
                        "id": "erp:get-order-context",
                        "method": "GET",
                        "path": "/orders/current",
                        "system_ref": "erp",
                    },
                    {
                        "id": "payment:get-payment",
                        "method": "GET",
                        "path": "/payments/{orderId}",
                        "system_ref": "payment",
                    },
                ],
                "actors": [{"id": "operator", "role": "public"}],
            },
        }
    )

    assert result["status"] == "COMPILED", result
    assert result["assertion"]["kind"] == "process_completion"
    assert [row["system_ref"] for row in result["treatment_plan"]] == [
        "erp",
        "payment",
    ]
    target = result["treatment_plan"][1]
    assert target["input_binding_refs"][0]["producer_output_field"] == "order_id"
    assert target["input_binding_refs"][0]["target"] == "orderId"
    assert result["per_step_evidence"] is True


def test_source_declared_cross_system_fan_in_compiles_one_exact_join() -> None:
    from ai_test_asset_center.multi_step_protocol import (
        compile_multi_step_process_protocol,
    )

    behaviors, bindings = _fan_in_bindings()
    graphs, unknowns, gate = build_business_process_graphs(
        _fan_in_model(), behaviors, bindings
    )

    assert unknowns == []
    assert gate["status"] == "PASS"
    graph = next(
        row for row in graphs if row["process_id"] == "process:purchase-fan-in"
    )
    assert graph["status"] == "COMPILED"
    assert len(graph["start_node_refs"]) == 2
    assert len(graph["join_groups"]) == 1
    join = graph["join_groups"][0]
    assert join["join_kind"] == "SOURCE_EXPLICIT_JOIN"
    assert join["status"] == "BOUND"
    actual_predecessors = {
        edge["source_node_id"]
        for edge in graph["edges"]
        if edge["target_node_id"] == join["join_node_id"]
    }
    assert set(join["predecessor_node_ids"]) == actual_predecessors
    assert len(actual_predecessors) == 2

    result = compile_multi_step_process_protocol(
        {
            "risk_family": "process",
            "operation_ref": graph["nodes"][0]["operation_ref"],
            "treatment_actor_ref": "operator",
            "property_spec": {
                "process_graph": graph,
                "source_refs": ["prd#purchase-fan-in"],
            },
            "behavior_ir": {
                "operations": [
                    {
                        "id": node["operation_ref"],
                        "method": node["method"],
                        "path": node["path"],
                        "system_ref": node["system_ref"],
                    }
                    for node in graph["nodes"]
                ],
                "actors": [{"id": "operator", "role": "public"}],
            },
        }
    )
    assert result["status"] == "COMPILED", result
    assert result["expected_order"] == []
    assert len(result["execution_graph"]["join_groups"]) == 1


def test_cross_system_fan_in_runtime_waits_for_every_declared_predecessor() -> None:
    behaviors, bindings = _fan_in_bindings()
    graphs, unknowns, _gate = build_business_process_graphs(
        _fan_in_model(), behaviors, bindings
    )
    assert unknowns == []
    graph = next(
        row for row in graphs if row["process_id"] == "process:purchase-fan-in"
    )
    plan = [
        {
            "step_id": node["node_id"],
            "operation_ref": node["operation_ref"],
            "actor_ref": node["actor_ref"],
            "system_ref": node["system_ref"],
            "input_binding_refs": deepcopy(node.get("input_binding_refs") or []),
            "output_binding_specs": deepcopy(node.get("output_binding_specs") or []),
            "_execution_graph": graph,
        }
        for node in graph["nodes"]
    ]
    ops = {
        node["operation_ref"]: {
            "id": node["operation_ref"],
            "method": node["method"],
            "path": node["path"],
        }
        for node in graph["nodes"]
    }
    runtime = prepare_graph_runtime(
        graph=graph,
        treatment_plan=plan,
        ops=ops,
        base_url="https://erp.example.test",
        runtime_contract={
            "status": "approved",
            "requested_base_url": "https://erp.example.test",
            "approved_base_url": "https://erp.example.test",
            "environment_type": "test",
            "environment_ref": "erp-test",
            "execution_mode": "safe_read_only",
            "system_ref": "erp",
            "approved_targets": {
                "inventory": {
                    "status": "approved",
                    "requested_base_url": "https://inventory.example.test",
                    "approved_base_url": "https://inventory.example.test",
                    "environment_type": "test",
                    "environment_ref": "inventory-test",
                    "execution_mode": "safe_read_only",
                    "actor_token_keys": {"operator": "inventory:operator"},
                },
                "procurement": {
                    "status": "approved",
                    "requested_base_url": "https://procurement.example.test",
                    "approved_base_url": "https://procurement.example.test",
                    "environment_type": "test",
                    "environment_ref": "procurement-test",
                    "execution_mode": "safe_read_only",
                    "actor_token_keys": {"operator": "procurement:operator"},
                },
            },
        },
    )
    assert runtime["status"] == "READY", runtime
    by_system = {row["system_ref"]: row for row in plan}
    order_step = by_system["erp"]
    stock_step = by_system["inventory"]
    purchase_step = by_system["procurement"]
    assert runtime["wave_by_node"][order_step["step_id"]] == 0
    assert runtime["wave_by_node"][stock_step["step_id"]] == 0
    assert runtime["wave_by_node"][purchase_step["step_id"]] == 1

    assert record_graph_step_outcome(
        runtime=runtime,
        graph=graph,
        step=order_step,
        observation={"status_code": 200, "body": {"data": {"orderId": "ORD-42"}}},
    )["status"] == "SUCCEEDED"
    blocked = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=purchase_step,
        initial_bindings={},
    )
    assert blocked["status"] == "BLOCKED"
    assert blocked["reason_code"] == "PROCESS_GRAPH_PREDECESSOR_NOT_SUCCEEDED"
    assert stock_step["step_id"] in blocked["detail"]

    assert record_graph_step_outcome(
        runtime=runtime,
        graph=graph,
        step=stock_step,
        observation={
            "status_code": 200,
            "body": {"data": {"stockCheckId": "STOCK-9"}},
        },
    )["status"] == "SUCCEEDED"
    ready = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=purchase_step,
        initial_bindings={},
    )
    assert ready["status"] == "READY"
    assert ready["base_url"] == "https://procurement.example.test"
    assert ready["bindings"] == {
        "orderId": "ORD-42",
        "stockCheckId": "STOCK-9",
    }


def test_runtime_blocks_join_metadata_that_disagrees_with_executable_edges() -> None:
    behaviors, bindings = _fan_in_bindings()
    graphs, unknowns, _gate = build_business_process_graphs(
        _fan_in_model(), behaviors, bindings
    )
    assert unknowns == []
    graph = deepcopy(
        next(row for row in graphs if row["process_id"] == "process:purchase-fan-in")
    )
    graph["join_groups"][0]["predecessor_node_ids"] = graph["join_groups"][0][
        "predecessor_node_ids"
    ][:1]
    plan = [
        {
            "step_id": node["node_id"],
            "operation_ref": node["operation_ref"],
            "actor_ref": node["actor_ref"],
            "system_ref": node["system_ref"],
            "_execution_graph": graph,
        }
        for node in graph["nodes"]
    ]
    result = prepare_graph_runtime(
        graph=graph,
        treatment_plan=plan,
        ops={
            node["operation_ref"]: {
                "id": node["operation_ref"],
                "method": node["method"],
                "path": node["path"],
            }
            for node in graph["nodes"]
        },
        base_url="https://erp.example.test",
        runtime_contract={
            "status": "approved",
            "requested_base_url": "https://erp.example.test",
            "approved_base_url": "https://erp.example.test",
            "environment_type": "test",
            "environment_ref": "erp-test",
            "execution_mode": "safe_read_only",
            "system_ref": "erp",
        },
    )
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "PROCESS_GRAPH_RUNTIME_INVALID"
    assert "join_1_predecessor_identity_invalid" in result["detail"]


def test_cross_system_fan_in_executes_through_existing_plan_executor(monkeypatch) -> None:
    from pathlib import Path

    import ai_test_asset_center.experiment_plan_executor as plan_executor
    from ai_test_asset_center.multi_step_protocol import (
        compile_multi_step_process_protocol,
    )
    from ai_test_asset_center.process_step_execution import ProcessStepLedger

    behaviors, bindings = _fan_in_bindings()
    graphs, unknowns, _gate = build_business_process_graphs(
        _fan_in_model(), behaviors, bindings
    )
    assert unknowns == []
    graph = next(
        row for row in graphs if row["process_id"] == "process:purchase-fan-in"
    )
    protocol = compile_multi_step_process_protocol(
        {
            "risk_family": "process",
            "operation_ref": graph["nodes"][0]["operation_ref"],
            "treatment_actor_ref": "operator",
            "property_spec": {"process_graph": graph},
            "behavior_ir": {
                "operations": [
                    {
                        "id": node["operation_ref"],
                        "method": node["method"],
                        "path": node["path"],
                        "system_ref": node["system_ref"],
                    }
                    for node in graph["nodes"]
                ],
                "actors": [{"id": "operator", "role": "operator"}],
            },
        }
    )
    assert protocol["status"] == "COMPILED", protocol
    calls: list[dict] = []

    def fake_sequential(**kwargs):
        step = kwargs["treatment_plan"][0]
        system_ref = step["system_ref"]
        calls.append(
            {
                "step_id": step["step_id"],
                "system_ref": system_ref,
                "base_url": kwargs["base_url"],
                "bindings": dict(kwargs["runtime_bindings"]),
            }
        )
        body = {
            "erp": {"data": {"orderId": "ORD-42"}},
            "inventory": {"data": {"stockCheckId": "STOCK-9"}},
            "procurement": {"ready": True},
        }[system_ref]
        ledger = ProcessStepLedger(
            experiment_id=kwargs["eid"],
            campaign_id=kwargs["resolved_campaign_id"],
            run_id=kwargs["resolved_execution_id"],
            obligation_id=kwargs["oid"],
            required_step_ids=[step["step_id"]],
        )
        ledger.record_step_execution(
            step_id=step["step_id"],
            phase="treatment",
            operation_ref=step["operation_ref"],
            actor_ref=step["actor_ref"],
            runtime_identity=dict(kwargs["runtime_bindings"]),
            status_code=200,
            final_status="EXECUTED",
            target_reached=True,
        )
        return {
            "steps": [
                {
                    "step_id": step["step_id"],
                    "phase": "treatment",
                    "operation_ref": step["operation_ref"],
                    "status_code": 200,
                    "body": body,
                }
            ],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [],
            "cleanup_failures": 0,
            "process_step_ledger": ledger,
        }

    monkeypatch.setattr(plan_executor, "_delegate_sequential", fake_sequential)
    observations: dict = {}
    result = plan_executor.execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=protocol["treatment_plan"],
        consumed_barrier_steps=set(),
        actors={
            "operator": {
                "id": "operator",
                "role": "operator",
                "credential_secret_ref": "erp:operator",
            }
        },
        ops={
            node["operation_ref"]: {
                "id": node["operation_ref"],
                "method": node["method"],
                "path": node["path"],
            }
            for node in graph["nodes"]
        },
        tokens={
            "erp:operator": "erp-token",
            "inventory:operator": "inventory-token",
            "procurement:operator": "procurement-token",
        },
        runtime_bindings={"tenantId": "TENANT-A"},
        activation_requirements={
            "control": [],
            "treatment": [
                row["step_id"] for row in protocol["treatment_plan"]
            ],
        },
        observations=observations,
        eid="exp-fan-in",
        oid="obl-fan-in",
        resolved_campaign_id="cmp-fan-in",
        resolved_execution_id="run-fan-in",
        campaign_id="cmp-fan-in",
        root=Path("."),
        project="project",
        base_url="https://erp.example.test",
        runtime_contract={
            "status": "approved",
            "requested_base_url": "https://erp.example.test",
            "approved_base_url": "https://erp.example.test",
            "environment_type": "test",
            "environment_ref": "erp-test",
            "execution_mode": "safe_read_only",
            "system_ref": "erp",
            "approved_targets": {
                "inventory": {
                    "status": "approved",
                    "requested_base_url": "https://inventory.example.test",
                    "approved_base_url": "https://inventory.example.test",
                    "environment_type": "test",
                    "environment_ref": "inventory-test",
                    "execution_mode": "safe_read_only",
                    "actor_token_keys": {"operator": "inventory:operator"},
                },
                "procurement": {
                    "status": "approved",
                    "requested_base_url": "https://procurement.example.test",
                    "approved_base_url": "https://procurement.example.test",
                    "environment_type": "test",
                    "environment_ref": "procurement-test",
                    "execution_mode": "safe_read_only",
                    "actor_token_keys": {"operator": "procurement:operator"},
                },
            },
        },
    )

    assert [row["system_ref"] for row in calls[-1:]] == ["procurement"]
    assert {row["system_ref"] for row in calls[:2]} == {"erp", "inventory"}
    assert calls[-1]["bindings"] == {
        "tenantId": "TENANT-A",
        "orderId": "ORD-42",
        "stockCheckId": "STOCK-9",
    }
    assert set(result["process_graph_runtime"]["node_status"].values()) == {
        "SUCCEEDED"
    }
    assert len(observations["graph_step_observations"]) == 3
