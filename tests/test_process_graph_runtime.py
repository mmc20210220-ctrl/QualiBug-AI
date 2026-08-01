from __future__ import annotations

from ai_test_asset_center.process_graph_runtime import (
    GRAPH_INPUT_BINDING_UNRESOLVED,
    GRAPH_OUTPUT_BINDING_UNRESOLVED,
    GRAPH_PREDECESSOR_NOT_SUCCEEDED,
    GRAPH_RUNTIME_ASYNC_UNSUPPORTED,
    GRAPH_TARGET_NOT_APPROVED,
    GRAPH_WRITE_RUNTIME_UNAVAILABLE,
    graph_step_context,
    prepare_graph_runtime,
    record_graph_step_outcome,
)


def _runtime_contract():
    return {
        "status": "approved",
        "requested_base_url": "https://erp.test.example",
        "approved_base_url": "https://erp.test.example",
        "environment_type": "test",
        "environment_ref": "erp-test",
        "execution_mode": "approved_sandbox_write",
        "system_ref": "erp",
        "approved_targets": {
            "payment": {
                "status": "approved",
                "requested_base_url": "https://payment.test.example",
                "approved_base_url": "https://payment.test.example",
                "environment_type": "test",
                "environment_ref": "payment-test",
                "execution_mode": "safe_read_only",
                "actor_token_keys": {
                    "actor_admin": "payment:actor_admin",
                },
            }
        },
    }


def _graph(relation_type="DEPENDS_ON", second_method="GET"):
    return {
        "execution_graph_id": "graph_order_payment",
        "process_id": "order_payment",
        "nodes": [
            {
                "node_id": "read_order",
                "step_id": "read_order",
                "operation_ref": "op_read_order",
                "actor_ref": "actor_admin",
                "system_ref": "erp",
                "method": "GET",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "order_id",
                        "json_path": "$.data.orderId",
                    }
                ],
            },
            {
                "node_id": "read_payment",
                "step_id": "read_payment",
                "operation_ref": "op_read_payment",
                "actor_ref": "actor_admin",
                "system_ref": "payment",
                "method": second_method,
                "input_binding_refs": [
                    {
                        "producer_node_id": "read_order",
                        "producer_output_field": "order_id",
                        "target": "orderId",
                    }
                ],
            },
        ],
        "edges": [
            {
                "source_node_id": "read_order",
                "target_node_id": "read_payment",
                "relation_type": relation_type,
            }
        ],
        "topological_order": ["read_order", "read_payment"],
        "wait_contracts": [],
    }


def _plan(graph):
    return [
        {
            "step_id": node["node_id"],
            "operation_ref": node["operation_ref"],
            "actor_ref": node["actor_ref"],
            "system_ref": node.get("system_ref", ""),
            "_execution_graph": graph,
        }
        for node in graph["nodes"]
    ]


def _ops(second_method="GET"):
    return {
        "op_read_order": {
            "id": "op_read_order",
            "method": "GET",
            "path": "/orders/current",
        },
        "op_read_payment": {
            "id": "op_read_payment",
            "method": second_method,
            "path": "/payments/{orderId}",
        },
    }


def _runtime(graph=None, ops=None):
    graph = graph or _graph()
    return prepare_graph_runtime(
        graph=graph,
        treatment_plan=_plan(graph),
        ops=ops or _ops(),
        base_url="https://erp.test.example",
        runtime_contract=_runtime_contract(),
    )


def test_sync_cross_system_read_targets_are_exact_and_ready():
    runtime = _runtime()
    assert runtime["status"] == "READY"
    assert runtime["wave_by_node"] == {
        "read_order": 0,
        "read_payment": 1,
    }
    payment = runtime["target_contexts"]["read_payment"]
    assert payment["base_url"] == "https://payment.test.example"
    assert payment["credential_token_key"] == "payment:actor_admin"
    assert payment["target_policy_decision"]["read_allowed"] is True


def test_every_graph_write_blocks_before_transport_until_graph_wide_closure():
    graph = _graph(second_method="POST")
    runtime = _runtime(graph=graph, ops=_ops("POST"))
    assert runtime["status"] == "BLOCKED"
    assert runtime["reason_code"] == GRAPH_WRITE_RUNTIME_UNAVAILABLE


def test_missing_target_is_not_replaced_by_connector_or_primary_url():
    graph = _graph()
    graph["nodes"][1]["system_ref"] = "warehouse"
    runtime = _runtime(graph=graph)
    assert runtime["status"] == "BLOCKED"
    assert runtime["reason_code"] == GRAPH_TARGET_NOT_APPROVED


def test_async_edge_remains_blocked_without_wait_observer_scheduler():
    graph = _graph(relation_type="TRIGGERS")
    runtime = _runtime(graph=graph)
    assert runtime["status"] == "BLOCKED"
    assert runtime["reason_code"] == GRAPH_RUNTIME_ASYNC_UNSUPPORTED


def test_declared_output_enters_namespaced_binding_ledger():
    graph = _graph()
    runtime = _runtime(graph=graph)
    result = record_graph_step_outcome(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[0],
        observation={
            "status_code": 200,
            "body": {"data": {"orderId": "ORD-9"}, "id": "wrong"},
        },
    )
    assert result["status"] == "SUCCEEDED"
    assert runtime["binding_ledger"]["outputs_by_node"]["read_order"][
        "order_id"
    ]["value"] == "ORD-9"
    assert "id" not in runtime["binding_ledger"]["outputs_by_node"][
        "read_order"
    ]


def test_missing_declared_output_blocks_the_producer_node():
    graph = _graph()
    runtime = _runtime(graph=graph)
    result = record_graph_step_outcome(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[0],
        observation={"status_code": 200, "body": {"data": {}}},
    )
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == GRAPH_OUTPUT_BINDING_UNRESOLVED
    assert runtime["node_status"]["read_order"] == "BLOCKED"


def test_consumer_receives_only_declared_producer_output():
    graph = _graph()
    runtime = _runtime(graph=graph)
    record_graph_step_outcome(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[0],
        observation={
            "status_code": 200,
            "body": {"data": {"orderId": "ORD-9"}},
        },
    )
    context = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[1],
        initial_bindings={"tenantId": "TENANT-A"},
    )
    assert context["status"] == "READY"
    assert context["bindings"] == {
        "tenantId": "TENANT-A",
        "orderId": "ORD-9",
    }


def test_declared_edge_binding_overrides_ambient_same_name_without_cross_system_pollution():
    graph = _graph()
    runtime = _runtime(graph=graph)
    record_graph_step_outcome(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[0],
        observation={
            "status_code": 200,
            "body": {"data": {"orderId": "ORD-9"}},
        },
    )

    context = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[1],
        initial_bindings={"orderId": "STALE-OTHER-SYSTEM"},
    )

    assert context["status"] == "READY"
    assert context["bindings"]["orderId"] == "ORD-9"
    consumption = runtime["binding_ledger"]["consumptions"][0]
    assert consumption["initial_binding_shadowed"] is True
    assert consumption["initial_value_fingerprint"]


def test_two_explicit_producers_cannot_disagree_on_one_consumer_target():
    graph = _graph()
    graph["nodes"].insert(1, {
        "node_id": "read_order_alias",
        "step_id": "read_order_alias",
        "operation_ref": "op_read_order",
        "actor_ref": "actor_admin",
        "system_ref": "erp",
        "method": "GET",
        "output_binding_specs": [
            {
                "canonical_field_id": "order_id",
                "json_path": "$.data.orderId",
            }
        ],
    })
    graph["topological_order"] = [
        "read_order",
        "read_order_alias",
        "read_payment",
    ]
    graph["edges"].append({
        "source_node_id": "read_order_alias",
        "target_node_id": "read_payment",
        "relation_type": "DEPENDS_ON",
    })
    graph["nodes"][2]["input_binding_refs"].append({
        "producer_node_id": "read_order_alias",
        "producer_output_field": "order_id",
        "target": "orderId",
    })
    runtime = prepare_graph_runtime(
        graph=graph,
        treatment_plan=_plan(graph),
        ops=_ops(),
        base_url="https://erp.test.example",
        runtime_contract=_runtime_contract(),
    )
    assert runtime["status"] == "READY"
    runtime["node_status"].update({
        "read_order": "SUCCEEDED",
        "read_order_alias": "SUCCEEDED",
    })
    runtime["binding_ledger"]["outputs_by_node"] = {
        "read_order": {
            "order_id": {"value": "ORD-9"},
        },
        "read_order_alias": {
            "order_id": {"value": "ORD-10"},
        },
    }

    context = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[2],
        initial_bindings={},
    )

    assert context["status"] == "BLOCKED"
    assert context["reason_code"] == "PROCESS_GRAPH_INPUT_BINDING_CONFLICT"


def test_missing_declared_output_value_blocks_consumer():
    graph = _graph()
    runtime = _runtime(graph=graph)
    runtime["node_status"]["read_order"] = "SUCCEEDED"
    context = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[1],
        initial_bindings={},
    )
    assert context["status"] == "BLOCKED"
    assert context["reason_code"] == GRAPH_INPUT_BINDING_UNRESOLVED


def test_failed_predecessor_blocks_join_or_successor():
    graph = _graph()
    runtime = _runtime(graph=graph)
    runtime["node_status"]["read_order"] = "FAILED"
    context = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=_plan(graph)[1],
        initial_bindings={},
    )
    assert context["status"] == "BLOCKED"
    assert context["reason_code"] == GRAPH_PREDECESSOR_NOT_SUCCEEDED
