from __future__ import annotations

from pathlib import Path

import ai_test_asset_center.experiment_plan_executor as plan_executor
from ai_test_asset_center.process_graph_cleanup_executor import (
    execute_process_graph_cleanup,
)
from ai_test_asset_center.process_graph_write_contract import (
    finalize_process_graph_write_contract,
)
from ai_test_asset_center.process_step_execution import ProcessStepLedger


def _source_ref(locator: str) -> list[dict]:
    return [{"source_id": "api", "kind": "api_operation", "locator": locator}]


def _behavior_ir() -> dict:
    return {
        "actors": [
            {
                "id": "actor_writer",
                "role": "writer",
                "credential_secret_ref": "erp:actor_writer",
            }
        ],
        "operations": [
            {
                "id": "op_create_order",
                "method": "POST",
                "path": "/orders",
                "read_write": "write",
                "system_ref": "erp",
                "source_refs": _source_ref("POST /orders"),
            },
            {
                "id": "op_list_orders",
                "method": "GET",
                "path": "/orders",
                "read_write": "read",
                "system_ref": "erp",
                "source_refs": _source_ref("GET /orders"),
            },
            {
                "id": "op_delete_order",
                "method": "DELETE",
                "path": "/orders/{order_id}",
                "read_write": "write",
                "system_ref": "erp",
                "source_refs": _source_ref("DELETE /orders/{order_id}"),
            },
            {
                "id": "op_create_payment",
                "method": "POST",
                "path": "/payments",
                "read_write": "write",
                "system_ref": "payment",
                "source_refs": _source_ref("POST /payments"),
            },
            {
                "id": "op_list_payments",
                "method": "GET",
                "path": "/payments",
                "read_write": "read",
                "system_ref": "payment",
                "source_refs": _source_ref("GET /payments"),
            },
            {
                "id": "op_delete_payment",
                "method": "DELETE",
                "path": "/payments/{payment_id}",
                "read_write": "write",
                "system_ref": "payment",
                "source_refs": _source_ref("DELETE /payments/{payment_id}"),
            },
        ],
        "relations": [
            {
                "id": "rel_delete_order",
                "relation_type": "compensates",
                "from_ref": "op_delete_order",
                "to_ref": "op_create_order",
                "operation_ref": "op_delete_order",
                "source_refs": [{"source_id": "prd", "locator": "undo order"}],
            },
            {
                "id": "rel_delete_payment",
                "relation_type": "compensates",
                "from_ref": "op_delete_payment",
                "to_ref": "op_create_payment",
                "operation_ref": "op_delete_payment",
                "source_refs": [{"source_id": "prd", "locator": "undo payment"}],
            },
        ],
    }


def _graph() -> dict:
    return {
        "schema_version": "qualibug.process-execution-graph.v1",
        "execution_graph_id": "graph_order_payment_write",
        "process_id": "order_payment_write",
        "nodes": [
            {
                "node_id": "create_order",
                "step_id": "create_order",
                "operation_ref": "op_create_order",
                "actor_ref": "actor_writer",
                "system_ref": "erp",
                "object_refs": ["order"],
                "method": "POST",
                "path": "/orders",
                "compensation_operation_ref": "op_delete_order",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "order_id",
                        "json_path": "$.order_id",
                    }
                ],
            },
            {
                "node_id": "create_payment",
                "step_id": "create_payment",
                "operation_ref": "op_create_payment",
                "actor_ref": "actor_writer",
                "system_ref": "payment",
                "object_refs": ["payment"],
                "method": "POST",
                "path": "/payments",
                "compensation_operation_ref": "op_delete_payment",
                "input_binding_refs": [
                    {
                        "producer_node_id": "create_order",
                        "producer_output_field": "order_id",
                        "target": "order_id",
                    }
                ],
                "output_binding_specs": [
                    {
                        "canonical_field_id": "payment_id",
                        "json_path": "$.payment_id",
                    }
                ],
            },
        ],
        "edges": [
            {
                "edge_id": "edge_order_payment",
                "source_node_id": "create_order",
                "target_node_id": "create_payment",
                "relation_type": "DEPENDS_ON",
                "binding_refs": [
                    {
                        "producer_node_id": "create_order",
                        "producer_output_field": "order_id",
                        "target": "order_id",
                    }
                ],
            }
        ],
        "topological_order": ["create_order", "create_payment"],
        "start_node_refs": ["create_order"],
        "terminal_node_refs": ["create_payment"],
        "wait_contracts": [],
    }


def _experiment() -> dict:
    graph = _graph()
    raw = {
        "experiment_id": "exp_cross_system_failure",
        "obligation_id": "obl_cross_system_failure",
        "compile_receipt": {"status": "COMPILED"},
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": node["node_id"],
                "operation_ref": node["operation_ref"],
                "actor_ref": node["actor_ref"],
                "system_ref": node["system_ref"],
                "method": node["method"],
                "path": node["path"],
                "_execution_graph": graph,
            }
            for node in graph["nodes"]
        ],
        "cleanup_plan": [],
        "observers": [{"observer_id": "http_response"}],
        "safety_contract": {},
    }
    return finalize_process_graph_write_contract(raw, _behavior_ir())


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
            }
        },
    }


def _governance(*, status: int, body: dict, before: list, after: list) -> dict:
    return {
        "accepted": 200 <= status < 300,
        "before": {"status": 200, "body": before},
        "write": {"status": status, "body": body},
        "after": {"status": 200, "body": after},
        "audit_path": "audit.jsonl",
        "audit_record": {"phase": "write", "status": status},
    }


def test_partial_cross_system_failure_compensates_only_observed_effect(
    monkeypatch,
    tmp_path: Path,
) -> None:
    exp = _experiment()
    assert exp["compile_receipt"]["status"] == "COMPILED"
    execution_calls: list[dict] = []

    def fake_sequential(**kwargs):
        step = kwargs["treatment_plan"][0]
        step_id = step["step_id"]
        execution_calls.append(
            {
                "step_id": step_id,
                "base_url": kwargs["base_url"],
                "bindings": dict(kwargs["runtime_bindings"]),
                "credential": kwargs["actors"]["actor_writer"][
                    "credential_secret_ref"
                ],
            }
        )
        if step_id == "create_order":
            status = 201
            body = {"order_id": "ORD-9"}
            governance = _governance(
                status=status,
                body=body,
                before=[],
                after=[{"order_id": "ORD-9"}],
            )
            request_body = {"customer_id": "CUS-1"}
        else:
            status = 500
            body = {"error": "payment unavailable"}
            governance = _governance(
                status=status,
                body=body,
                before=[],
                after=[],
            )
            request_body = {"order_id": kwargs["runtime_bindings"]["order_id"]}

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
            status_code=status,
            final_status="EXECUTED",
            target_reached=True,
        )
        return {
            "steps": [
                {
                    "phase": "treatment",
                    "step_id": step_id,
                    "operation_ref": step["operation_ref"],
                    "actor_ref": step["actor_ref"],
                    "system_ref": step["system_ref"],
                    "method": "POST",
                    "path": step["path"],
                    "status_code": status,
                    "body": body,
                    "governance_receipt": governance,
                }
            ],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {step_id: request_body},
            "pre_transport_block_reasons": [],
            "cleanup_failures": 0,
            "process_step_ledger": ledger,
        }

    monkeypatch.setattr(plan_executor, "_delegate_sequential", fake_sequential)
    observations: dict = {}
    result = plan_executor.execute_non_barrier_plans(
        control_plan=exp["control_plan"],
        treatment_plan=exp["treatment_plan"],
        consumed_barrier_steps=set(),
        actors={
            "actor_writer": {
                "id": "actor_writer",
                "role": "writer",
                "credential_secret_ref": "erp:actor_writer",
            }
        },
        ops={row["id"]: row for row in _behavior_ir()["operations"]},
        tokens={
            "erp:actor_writer": "erp-token",
            "payment:actor_writer": "payment-token",
        },
        # Deliberately stale ambient value: the graph edge must override it.
        runtime_bindings={"order_id": "STALE-OTHER-SYSTEM"},
        activation_requirements={
            "control": [],
            "treatment": ["create_order", "create_payment"],
        },
        observations=observations,
        eid=exp["experiment_id"],
        oid=exp["obligation_id"],
        resolved_campaign_id="campaign",
        resolved_execution_id="execution",
        campaign_id="campaign",
        root=tmp_path,
        project="project",
        base_url="https://erp.test.example",
        runtime_contract=_runtime_contract(),
    )

    assert result["process_graph_runtime"]["node_status"] == {
        "create_order": "SUCCEEDED",
        "create_payment": "FAILED",
    }
    assert execution_calls == [
        {
            "step_id": "create_order",
            "base_url": "https://erp.test.example",
            "bindings": {"order_id": "STALE-OTHER-SYSTEM"},
            "credential": "erp:actor_writer",
        },
        {
            "step_id": "create_payment",
            "base_url": "https://payment.test.example",
            "bindings": {"order_id": "ORD-9"},
            "credential": "payment:actor_writer",
        },
    ]

    cleanup_calls: list[dict] = []

    def fake_cleanup_write(**kwargs):
        cleanup_calls.append(kwargs)
        assert kwargs["path"] == "/orders/ORD-9"
        return {
            "accepted": True,
            "before": {
                "status": 200,
                "body": [{"order_id": "ORD-9"}],
            },
            "write": {"status": 204, "body": {}},
            "after": {"status": 200, "body": []},
            "audit_path": "audit.jsonl",
            "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
        }

    cleanup = execute_process_graph_cleanup(
        exp=exp,
        steps_out=result["steps"],
        observations=observations,
        contract_evidence_receipts=result["contract_evidence_receipts"],
        request_bodies_for_cleanup=result["request_bodies_for_cleanup"],
        # Another subsystem has polluted both common names; source-step
        # evidence must remain authoritative for compensation.
        runtime_bindings={
            "order_id": "STALE-OTHER-SYSTEM",
            "payment_id": "STALE-PAYMENT",
        },
        cleanup_failures=0,
        actors={
            "actor_writer": {
                "id": "actor_writer",
                "role": "writer",
                "credential_secret_ref": "erp:actor_writer",
            }
        },
        tokens={
            "erp:actor_writer": "erp-token",
            "payment:actor_writer": "payment-token",
        },
        eid=exp["experiment_id"],
        oid=exp["obligation_id"],
        resolved_campaign_id="campaign",
        resolved_execution_id="execution",
        campaign_id="campaign",
        root=tmp_path,
        project="project",
        base_url="https://erp.test.example",
        runtime_contract=_runtime_contract(),
        execute_governed_control_write=fake_cleanup_write,
        sandbox_write_allowed=lambda **kwargs: (True, ""),
    )

    assert cleanup["cleanup_failures"] == 0
    assert len(cleanup_calls) == 1
    assert cleanup_calls[0]["base_url"] == "https://erp.test.example"
    assert cleanup_calls[0]["actor_token"] == "erp-token"
    assert cleanup["observations"]["process_graph_rollback_outcomes"] == {
        "create_payment": "NOT_REQUIRED",
        "create_order": "COMPLETED",
    }
