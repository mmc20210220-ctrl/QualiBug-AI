from __future__ import annotations

from pathlib import Path

import ai_test_asset_center.cross_entity_chain_planning as cross_planning
import ai_test_asset_center.experiment_plan_executor as plan_executor
from ai_test_asset_center.cross_entity_chain_planning import (
    CROSS_ENTITY_BINDING_UNRESOLVED,
    CROSS_ENTITY_OPERATION_UNRESOLVED,
    CROSS_ENTITY_RELATION_SCOPE_AMBIGUOUS,
    build_cross_entity_planning_context,
    plan_cross_entity_experiments,
)
from ai_test_asset_center.deep_experiment_planner import (
    MECHANISM_CROSS_ENTITY_PROCESS_GRAPH,
    plan_deep_experiments,
)
from ai_test_asset_center.process_step_execution import ProcessStepLedger


def _behavior_ir() -> dict:
    return {
        "actors": [
            {
                "id": "actor_admin",
                "role": "admin",
                "credential_secret_ref": "primary:actor_admin",
            }
        ],
        "operations": [
            {
                "operation_id": "op_read_order",
                "method": "GET",
                "path": "/orders/current",
                "system_ref": "erp",
                "object_refs": ["order"],
            },
            {
                "interface_id": "op_read_payment",
                "method": "GET",
                "path": "/payments/{orderId}",
                "system_ref": "payment",
                "object_refs": ["payment"],
            },
        ],
        "process_graphs": [
            {
                "execution_graph_id": "graph_order_payment",
                "process_id": "order_payment",
                "nodes": [
                    {
                        "node_id": "read_order",
                        "operation_ref": "op_read_order",
                        "actor_ref": "actor_admin",
                        "system_ref": "erp",
                        "object_refs": ["order"],
                        "output_binding_specs": [
                            {
                                "canonical_field_id": "order_id",
                                "json_path": "$.data.orderId",
                            }
                        ],
                    },
                    {
                        "node_id": "read_payment",
                        "operation_ref": "op_read_payment",
                        "actor_ref": "actor_admin",
                        "system_ref": "payment",
                        "object_refs": ["payment"],
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
                        "relation_type": "DEPENDS_ON",
                        "binding_refs": [
                            {
                                "producer_node_id": "read_order",
                                "producer_output_field": "order_id",
                                "target": "orderId",
                            }
                        ],
                    }
                ],
                "topological_order": ["read_order", "read_payment"],
                "start_node_refs": ["read_order"],
                "terminal_node_refs": ["read_payment"],
                "wait_contracts": [],
            }
        ],
    }


def _graph_obligation() -> dict:
    return {
        "obligation_id": "obl_order_payment",
        "risk_family": "cross_system",
        "process_graph_ref": "order_payment",
        "required_entities": ["order", "payment"],
        "required_operations": ["op_read_order", "op_read_payment"],
        "property": {"actor_ref": "actor_admin"},
        "source_refs": ["prd:order-payment:1"],
    }


def _runtime_contract() -> dict:
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


def test_source_graph_compiles_exact_systems_bindings_and_operation_aliases():
    result = plan_cross_entity_experiments(_graph_obligation(), _behavior_ir())

    assert result["status"] == "EXPLORED"
    graph = result["execution_graph"]
    assert graph["topological_order"] == ["read_order", "read_payment"]
    assert [node["system_ref"] for node in graph["nodes"]] == ["erp", "payment"]
    assert [node["path"] for node in graph["nodes"]] == [
        "/orders/current",
        "/payments/{orderId}",
    ]
    assert graph["nodes"][1]["input_binding_refs"] == [
        {
            "producer_node_id": "read_order",
            "producer_output_field": "order_id",
            "target": "orderId",
        }
    ]
    assert all(step["_execution_graph"] == graph for step in result["treatment_plan"])
    assert result["chain_proof"]["execution_ready"] is True
    assert result["dependency_proof"]["edges"][0]["binding_ref_count"] == 1


def test_async_cross_system_graph_reuses_public_wait_contract_authority():
    ir = _behavior_ir()
    ir["operations"].append(
        {
            "id": "op_wait_payment",
            "method": "GET",
            "read_write": "read",
            "path": "/payments/{orderId}/status",
            "system_ref": "payment",
        }
    )
    graph = ir["process_graphs"][0]
    graph["edges"][0]["relation_type"] = "AWAITS"
    graph["wait_contracts"] = [
        {
            "wait_id": "wait_payment_ready",
            "source_node_id": "read_order",
            "target_node_id": "read_payment",
            "observer_operation_ref": "op_wait_payment",
            "actor_ref": "actor_admin",
            "system_ref": "payment",
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
                "required_stable_observations": 1,
                "terminal_condition": "source_declared_predicate",
            },
        }
    ]

    result = plan_cross_entity_experiments(_graph_obligation(), ir)

    assert result["status"] == "EXPLORED"
    runtime = result["execution_graph"]["wait_runtime_contract"]
    assert runtime["status"] == "COMPILED"
    assert runtime["contract_count"] == 1
    wait = result["execution_graph"]["wait_contracts_by_target"][
        "read_payment"
    ]
    assert wait["observer_operation_ref"] == "op_wait_payment"
    assert wait["system_ref"] == "payment"
    assert result["treatment_plan"][1]["wait_contract"] == wait


def test_relation_chain_fails_closed_when_cross_object_identity_binding_is_missing():
    ir = {
        "operations": [
            {
                "id": "op_parent",
                "method": "GET",
                "path": "/parents/current",
                "object_refs": ["parent"],
            },
            {
                "id": "op_child",
                "method": "GET",
                "path": "/children/{parentId}",
                "object_refs": ["child"],
            },
        ],
        "relations": [
            {
                "id": "rel_parent_child",
                "type": "REFERENCES",
                "from_entity": "parent",
                "to_entity": "child",
            }
        ],
    }
    obligation = {
        "obligation_id": "obl_relation",
        "risk_family": "cross_object",
        "relation_id": "rel_parent_child",
        "required_entities": ["parent", "child"],
        "required_operations": ["op_parent", "op_child"],
        "property": {"actor_ref": "actor_admin"},
    }

    result = plan_cross_entity_experiments(obligation, ir)

    assert result["status"] == "INSUFFICIENT_INFO"
    assert result["reason"] == CROSS_ENTITY_BINDING_UNRESOLVED
    assert any(
        blocker["reason_code"] == CROSS_ENTITY_BINDING_UNRESOLVED
        for blocker in result["blockers"]
    )
    assert result["direct_db_write"] is False


def test_relation_chain_compiles_only_with_source_declared_output_and_consumer_target():
    ir = {
        "operations": [
            {
                "id": "op_parent",
                "method": "GET",
                "path": "/parents/current",
                "object_refs": ["parent"],
            },
            {
                "id": "op_child",
                "method": "GET",
                "path": "/children/{parentId}",
                "object_refs": ["child"],
            },
        ],
        "relations": [
            {
                "id": "rel_parent_child",
                "type": "REFERENCES",
                "from_entity": "parent",
                "to_entity": "child",
                "canonical_field_id": "parent_id",
                "source_json_path": "$.data.parentId",
                "consumer_target": "parentId",
            }
        ],
    }
    obligation = {
        "obligation_id": "obl_relation",
        "risk_family": "cross_object",
        "relation_id": "rel_parent_child",
        "required_entities": ["parent", "child"],
        "required_operations": ["op_parent", "op_child"],
        "property": {"actor_ref": "actor_admin"},
    }

    result = plan_cross_entity_experiments(obligation, ir)

    assert result["status"] == "EXPLORED"
    graph = result["execution_graph"]
    assert graph["nodes"][0]["output_binding_specs"] == [
        {"canonical_field_id": "parent_id", "json_path": "$.data.parentId"}
    ]
    assert graph["nodes"][1]["input_binding_refs"] == [
        {
            "producer_node_id": graph["nodes"][0]["node_id"],
            "producer_output_field": "parent_id",
            "target": "parentId",
        }
    ]


def test_relation_projection_rejects_multi_edge_process_without_source_graph():
    ir = {
        "operations": [
            {"id": "op_a", "method": "GET", "path": "/a"},
            {"id": "op_b", "method": "GET", "path": "/b"},
            {"id": "op_c", "method": "GET", "path": "/c"},
        ],
        "relations": [
            {
                "id": "rel_ab",
                "type": "SEQUENCE",
                "from_entity": "a",
                "to_entity": "b",
            }
        ],
    }
    obligation = {
        "obligation_id": "obl_three_steps",
        "risk_family": "cross_object",
        "relation_id": "rel_ab",
        "required_entities": ["a", "b", "c"],
        "required_operations": ["op_a", "op_b", "op_c"],
        "property": {"actor_ref": "actor_admin"},
    }

    result = plan_cross_entity_experiments(obligation, ir)

    assert result["status"] == "INSUFFICIENT_INFO"
    assert result["reason"] == CROSS_ENTITY_RELATION_SCOPE_AMBIGUOUS


def test_source_graph_cannot_reference_operation_missing_from_behavior_ir():
    ir = _behavior_ir()
    ir["process_graphs"][0]["nodes"][1]["operation_ref"] = "op_unknown"
    obligation = _graph_obligation()
    obligation["required_operations"] = ["op_read_order", "op_unknown"]

    result = plan_cross_entity_experiments(obligation, ir)

    assert result["status"] == "INSUFFICIENT_INFO"
    assert result["reason"] == CROSS_ENTITY_OPERATION_UNRESOLVED
    assert "op_unknown" in result["blockers"][0]["detail"]


def test_deep_planner_accepts_graph_without_duplicate_top_level_operation_and_keeps_metadata_clean():
    obligation = _graph_obligation()
    obligation["property"].pop("operation_ref", None)

    result = plan_deep_experiments([obligation], {}, _behavior_ir())

    assert result["planned_count"] == 1
    experiment = result["deep_experiments"][0]
    assert experiment["mechanism"] == MECHANISM_CROSS_ENTITY_PROCESS_GRAPH
    assert experiment["execution_graph"]["execution_graph_id"] == "graph_order_payment"
    assert [step["step_id"] for step in experiment["treatment_plan"]] == [
        "read_order",
        "read_payment",
    ]
    assert experiment["compile_receipt"]["process_graph_compiled"] is True
    assert experiment["compile_receipt"]["actor_matrix_expanded"] is False
    assert experiment["actor_matrix_result"] is None
    assert experiment["process_graph_result"] == "CROSS_ENTITY_PROCESS_GRAPH_COMPILED"
    assert experiment["chain_proof"]["execution_ready"] is True


def test_one_run_context_prevents_per_obligation_ir_reindex(monkeypatch):
    ir = _behavior_ir()
    context = build_cross_entity_planning_context(ir)

    def fail_reindex(_ir):
        raise AssertionError("planning context was not reused")

    monkeypatch.setattr(cross_planning, "_operation_index", fail_reindex)
    monkeypatch.setattr(cross_planning, "_relation_index", fail_reindex)

    for index in range(25):
        obligation = _graph_obligation()
        obligation["obligation_id"] = f"obl_{index}"
        result = plan_cross_entity_experiments(
            obligation,
            ir,
            context=context,
        )
        assert result["status"] == "EXPLORED"


def test_planner_output_runs_end_to_end_across_exact_targets_and_binding_ledger(monkeypatch):
    deep = plan_deep_experiments([_graph_obligation()], {}, _behavior_ir())
    experiment = deep["deep_experiments"][0]
    calls: list[dict] = []

    def fake_sequential(**kwargs):
        step = kwargs["treatment_plan"][0]
        step_id = step["step_id"]
        calls.append(
            {
                "step_id": step_id,
                "base_url": kwargs["base_url"],
                "runtime_bindings": dict(kwargs["runtime_bindings"]),
                "credential_secret_ref": kwargs["actors"]["actor_admin"][
                    "credential_secret_ref"
                ],
            }
        )
        body = (
            {"data": {"orderId": "ORD-9"}}
            if step_id == "read_order"
            else {"paymentStatus": "PAID"}
        )
        ledger = ProcessStepLedger(
            experiment_id=kwargs["eid"],
            campaign_id=kwargs["resolved_campaign_id"],
            run_id=kwargs["resolved_execution_id"],
            obligation_id=kwargs["oid"],
            required_step_ids=[step_id],
        )
        ledger.record_step_execution(
            step_id=step_id,
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
                    "step_id": step_id,
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
        control_plan=experiment["control_plan"],
        treatment_plan=experiment["treatment_plan"],
        consumed_barrier_steps=set(),
        actors={
            "actor_admin": {
                "id": "actor_admin",
                "role": "admin",
                "credential_secret_ref": "primary:actor_admin",
            }
        },
        ops={
            "op_read_order": {
                "id": "op_read_order",
                "method": "GET",
                "path": "/orders/current",
            },
            "op_read_payment": {
                "id": "op_read_payment",
                "method": "GET",
                "path": "/payments/{orderId}",
            },
        },
        tokens={
            "primary:actor_admin": "token-primary",
            "payment:actor_admin": "token-payment",
        },
        runtime_bindings={"tenantId": "TENANT-A"},
        activation_requirements={
            "control": [],
            "treatment": ["read_order", "read_payment"],
        },
        observations=observations,
        eid=experiment["experiment_id"],
        oid=experiment["obligation_id"],
        resolved_campaign_id="cmp-1",
        resolved_execution_id="run-1",
        campaign_id="cmp-1",
        root=Path("."),
        project="project",
        base_url="https://erp.test.example",
        runtime_contract=_runtime_contract(),
    )

    assert [call["base_url"] for call in calls] == [
        "https://erp.test.example",
        "https://payment.test.example",
    ]
    assert calls[1]["runtime_bindings"] == {
        "tenantId": "TENANT-A",
        "orderId": "ORD-9",
    }
    assert calls[1]["credential_secret_ref"] == "payment:actor_admin"
    assert result["process_graph_runtime"]["node_status"] == {
        "read_order": "SUCCEEDED",
        "read_payment": "SUCCEEDED",
    }
    output = observations["process_graph_binding_ledger"]["outputs_by_node"][
        "read_order"
    ]["order_id"]
    assert "value" not in output
    assert output["value_fingerprint"]
