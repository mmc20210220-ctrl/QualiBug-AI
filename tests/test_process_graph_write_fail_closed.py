from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.process_graph_cleanup_executor import (
    execute_process_graph_cleanup,
)


def test_rejected_but_effectful_write_is_compensated(tmp_path: Path) -> None:
    exp = {
        "experiment_id": "exp-effectful-rejection",
        "process_graph_write_contract": {
            "status": "RESOLVED",
            "write_step_ids": ["create-order"],
            "cleanup_steps": [
                {
                    "step_id": "cleanup_create-order",
                    "source_step_id": "create-order",
                    "source_operation_ref": "op-create-order",
                    "compensates_operation_ref": "op-create-order",
                    "operation_ref": "op-delete-order",
                    "actor_ref": "actor-writer",
                    "system_ref": "orders",
                    "method": "DELETE",
                    "path": "/orders/{order_id}",
                    "action": "source_declared_compensation",
                    "mode": "compensating_transition",
                    "source_declared": True,
                    "binding_specs": [
                        {
                            "target": "order_id",
                            "source": "write_response",
                            "source_path": "$.order_id",
                            "canonical_field_id": "order_id",
                        }
                    ],
                    "observer_operation": {
                        "operation_ref": "op-list-orders",
                        "method": "GET",
                        "path": "/orders",
                    },
                }
            ],
        },
    }
    source = {
        "phase": "treatment",
        "step_id": "create-order",
        "operation_ref": "op-create-order",
        "actor_ref": "actor-writer",
        "system_ref": "orders",
        "method": "POST",
        "path": "/orders",
        "status_code": 409,
        "body": {"order_id": "ORD-409"},
        "governance_receipt": {
            "accepted": False,
            "before": {"status": 200, "body": []},
            "write": {"status": 409, "body": {"order_id": "ORD-409"}},
            "after": {"status": 200, "body": [{"order_id": "ORD-409"}]},
            "audit_path": "audit.jsonl",
            "audit_record": {"phase": "write", "order_id": "ORD-409"},
        },
    }
    runtime_contract = {
        "status": "approved",
        "system_ref": "orders",
        "requested_base_url": "https://orders.test.example",
        "approved_base_url": "https://orders.test.example",
        "environment_type": "test",
        "environment_ref": "orders-test",
        "execution_mode": "approved_sandbox_write",
    }
    calls: list[dict] = []

    def governed(**kwargs):
        calls.append(kwargs)
        return {
            "accepted": True,
            "before": {"status": 200, "body": [{"order_id": "ORD-409"}]},
            "write": {"status": 204, "body": {}},
            "after": {"status": 200, "body": []},
            "audit_path": "audit.jsonl",
            "audit_record": {"phase": "cleanup", "path": kwargs["path"]},
        }

    result = execute_process_graph_cleanup(
        exp=exp,
        steps_out=[source],
        observations={},
        contract_evidence_receipts=[],
        request_bodies_for_cleanup={"create-order": {"sku": "SKU-1"}},
        runtime_bindings={},
        cleanup_failures=0,
        actors={"actor-writer": {"id": "actor-writer", "role": "public"}},
        tokens={},
        eid="exp-effectful-rejection",
        oid="obl-effectful-rejection",
        resolved_campaign_id="campaign",
        resolved_execution_id="execution",
        campaign_id="campaign",
        root=tmp_path,
        project="project",
        base_url="https://orders.test.example",
        runtime_contract=runtime_contract,
        execute_governed_control_write=governed,
        sandbox_write_allowed=lambda **kwargs: (True, ""),
    )

    assert len(calls) == 1
    assert calls[0]["path"] == "/orders/ORD-409"
    assert result["cleanup_failures"] == 0
    receipt = result["process_graph_cleanup_receipts"][0]
    assert receipt["status"] == "COMPLETED"
    assert receipt["evidence"]["source_status_code"] == 409
    assert receipt["evidence"]["effectful_write_count"] == 1
