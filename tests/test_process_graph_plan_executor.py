from __future__ import annotations

from pathlib import Path

import ai_test_asset_center.experiment_plan_executor as plan_executor
from ai_test_asset_center.process_step_execution import ProcessStepLedger


def _graph():
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
                "method": "GET",
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
            }
        ],
        "topological_order": ["read_order", "read_payment"],
        "wait_contracts": [],
    }


def _plan():
    graph = _graph()
    return [
        {
            "step_id": node["node_id"],
            "operation_ref": node["operation_ref"],
            "actor_ref": node["actor_ref"],
            "system_ref": node["system_ref"],
            "_execution_graph": graph,
        }
        for node in graph["nodes"]
    ]


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


def _call(monkeypatch, *, first_status=200, tokens=None):
    calls = []

    def fake_sequential(**kwargs):
        step = kwargs["treatment_plan"][0]
        step_id = step["step_id"]
        calls.append(
            {
                "step_id": step_id,
                "base_url": kwargs["base_url"],
                "runtime_bindings": dict(kwargs["runtime_bindings"]),
                "actor": dict(kwargs["actors"]["actor_admin"]),
            }
        )
        status = first_status if step_id == "read_order" else 200
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
            status_code=status,
            final_status="EXECUTED",
            target_reached=True,
        )
        return {
            "steps": [
                {
                    "step_id": step_id,
                    "phase": "treatment",
                    "operation_ref": step["operation_ref"],
                    "status_code": status,
                    "body": body,
                    "path": (
                        "/orders/current"
                        if step_id == "read_order"
                        else f"/payments/{kwargs['runtime_bindings'].get('orderId', '')}"
                    ),
                }
            ],
            "contract_evidence_receipts": [],
            "request_bodies_for_cleanup": {},
            "pre_transport_block_reasons": [],
            "cleanup_failures": 0,
            "process_step_ledger": ledger,
        }

    monkeypatch.setattr(plan_executor, "_delegate_sequential", fake_sequential)
    observations = {}
    result = plan_executor.execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=_plan(),
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
        tokens=tokens
        or {
            "primary:actor_admin": "token-primary",
            "payment:actor_admin": "token-payment",
        },
        runtime_bindings={"tenantId": "TENANT-A"},
        activation_requirements={
            "control": [],
            "treatment": ["read_order", "read_payment"],
        },
        observations=observations,
        eid="exp-1",
        oid="obl-1",
        resolved_campaign_id="cmp-1",
        resolved_execution_id="run-1",
        campaign_id="cmp-1",
        root=Path("."),
        project="project",
        base_url="https://erp.test.example",
        runtime_contract=_runtime_contract(),
    )
    return result, observations, calls


def test_main_entry_dispatches_exact_targets_and_namespaced_binding(monkeypatch):
    result, observations, calls = _call(monkeypatch)
    assert [call["base_url"] for call in calls] == [
        "https://erp.test.example",
        "https://payment.test.example",
    ]
    assert calls[0]["runtime_bindings"] == {"tenantId": "TENANT-A"}
    assert calls[1]["runtime_bindings"] == {
        "tenantId": "TENANT-A",
        "orderId": "ORD-9",
    }
    assert calls[1]["actor"]["credential_secret_ref"] == "payment:actor_admin"
    assert result["process_graph_runtime"]["node_status"] == {
        "read_order": "SUCCEEDED",
        "read_payment": "SUCCEEDED",
    }
    output = observations["process_graph_binding_ledger"]["outputs_by_node"][
        "read_order"
    ]["order_id"]
    assert "value" not in output
    assert output["value_fingerprint"]


def test_failed_predecessor_blocks_downstream_transport(monkeypatch):
    result, _observations, calls = _call(monkeypatch, first_status=500)
    assert [call["step_id"] for call in calls] == ["read_order"]
    assert result["process_graph_runtime"]["node_status"] == {
        "read_order": "FAILED",
        "read_payment": "BLOCKED",
    }
    assert any(
        step.get("step_id") == "read_payment"
        and step.get("reason") == "PROCESS_GRAPH_PREDECESSOR_NOT_SUCCEEDED"
        for step in result["steps"]
    )


def test_secondary_target_without_its_token_never_reuses_primary_token(monkeypatch):
    result, _observations, calls = _call(
        monkeypatch,
        tokens={"primary:actor_admin": "token-primary"},
    )
    assert [call["step_id"] for call in calls] == ["read_order"]
    assert result["process_graph_runtime"]["node_status"] == {
        "read_order": "SUCCEEDED",
        "read_payment": "BLOCKED",
    }
    assert any(
        step.get("step_id") == "read_payment"
        and step.get("reason")
        == "PROCESS_GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED"
        for step in result["steps"]
    )


def test_non_graph_plan_delegates_to_original_kernel(monkeypatch):
    from ai_test_asset_center import experiment_plan_lifecycle_adapter

    sentinel = {"steps": [{"step_id": "legacy"}]}

    def fake_sequential(**_kwargs):
        return sentinel

    monkeypatch.setattr(plan_executor, "_delegate_sequential", fake_sequential)
    result = experiment_plan_lifecycle_adapter.execute_non_barrier_plans(
        control_plan=[],
        treatment_plan=[{"step_id": "legacy", "operation_ref": "op_legacy"}],
        consumed_barrier_steps=set(),
        actors={},
        ops={},
        tokens={},
        runtime_bindings={},
        activation_requirements={},
        observations={},
        eid="e",
        oid="o",
        resolved_campaign_id="c",
        resolved_execution_id="r",
        campaign_id="c",
        root=Path("."),
        project="p",
        base_url="https://example.test",
        runtime_contract={},
    )
    assert result["steps"] == sentinel["steps"]
    assert result["pre_transport_block_reasons"] == []
    assert result["request_build_first_loss_receipt"]["status"] == "NOT_APPLICABLE"
