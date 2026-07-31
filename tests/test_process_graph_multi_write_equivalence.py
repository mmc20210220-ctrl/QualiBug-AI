from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.cleanup_equivalence import (
    evaluate_cleanup_equivalence,
)
from ai_test_asset_center.process_graph_cleanup_equivalence import (
    finalize_process_graph_cleanup_equivalence_inputs,
)
from ai_test_asset_center.process_graph_reversibility import (
    GRAPH_REVERSIBILITY_SCHEMA,
    finalize_process_graph_reversibility,
    validate_process_graph_reversibility_runtime,
)
from ai_test_asset_center.process_graph_write_contract import (
    finalize_process_graph_write_contract,
)


def _source_ref(locator: str) -> list[dict]:
    return [
        {
            "source_id": "api",
            "kind": "api_operation",
            "locator": locator,
        }
    ]


def _behavior_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op-create-order",
                "method": "POST",
                "path": "/orders",
                "system_ref": "orders",
                "request_example": {"sku": "SKU-1"},
                "source_refs": _source_ref("POST /orders"),
            },
            {
                "id": "op-list-orders",
                "method": "GET",
                "path": "/orders",
                "system_ref": "orders",
                "source_refs": _source_ref("GET /orders"),
            },
            {
                "id": "op-delete-order",
                "method": "DELETE",
                "path": "/orders/{order_id}",
                "system_ref": "orders",
                "source_refs": _source_ref("DELETE /orders/{order_id}"),
            },
            {
                "id": "op-create-payment",
                "method": "POST",
                "path": "/payments",
                "system_ref": "payments",
                "request_example": {"order_id": "{order_id}"},
                "source_refs": _source_ref("POST /payments"),
            },
            {
                "id": "op-list-payments",
                "method": "GET",
                "path": "/payments",
                "system_ref": "payments",
                "source_refs": _source_ref("GET /payments"),
            },
            {
                "id": "op-delete-payment",
                "method": "DELETE",
                "path": "/payments/{payment_id}",
                "system_ref": "payments",
                "source_refs": _source_ref("DELETE /payments/{payment_id}"),
            },
        ],
        "actors": [
            {
                "id": "actor-writer",
                "role": "public",
            }
        ],
        "relations": [
            {
                "id": "rel-delete-order",
                "relation_type": "compensates",
                "from_ref": "op-delete-order",
                "to_ref": "op-create-order",
                "operation_ref": "op-delete-order",
                "source_refs": _source_ref("order compensation"),
            },
            {
                "id": "rel-delete-payment",
                "relation_type": "compensates",
                "from_ref": "op-delete-payment",
                "to_ref": "op-create-payment",
                "operation_ref": "op-delete-payment",
                "source_refs": _source_ref("payment compensation"),
            },
        ],
    }


def _graph() -> dict:
    return {
        "schema_version": "qualibug.process-execution-graph.v1",
        "execution_graph_id": "graph-order-payment",
        "process_id": "process-order-payment",
        "nodes": [
            {
                "node_id": "create-order",
                "step_id": "create-order",
                "operation_ref": "op-create-order",
                "actor_ref": "actor-writer",
                "system_ref": "orders",
                "method": "POST",
                "path": "/orders",
                "compensation_operation_ref": "op-delete-order",
                "output_binding_specs": [
                    {
                        "canonical_field_id": "order_id",
                        "json_path": "$.order_id",
                    }
                ],
            },
            {
                "node_id": "create-payment",
                "step_id": "create-payment",
                "operation_ref": "op-create-payment",
                "actor_ref": "actor-writer",
                "system_ref": "payments",
                "method": "POST",
                "path": "/payments",
                "compensation_operation_ref": "op-delete-payment",
                "input_binding_specs": [
                    {
                        "producer_node_id": "create-order",
                        "source_field": "order_id",
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
                "edge_id": "edge-order-payment",
                "source_node_id": "create-order",
                "target_node_id": "create-payment",
                "relation_type": "DEPENDS_ON",
            }
        ],
        "topological_order": ["create-order", "create-payment"],
        "start_node_ids": ["create-order"],
        "terminal_node_ids": ["create-payment"],
        "wait_contracts": [],
    }


def _experiment() -> dict:
    graph = _graph()
    return {
        "experiment_id": "exp-order-payment",
        "obligation_id": "obl-order-payment",
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
        "safety_contract": {"governed_write": False},
        "source_refs": [
            {
                "source_id": "requirements",
                "locator": "order payment flow",
            }
        ],
    }


def _compiled() -> dict:
    result = finalize_process_graph_write_contract(
        _experiment(),
        _behavior_ir(),
    )
    assert result["compile_receipt"]["status"] == "COMPILED", result[
        "compile_receipt"
    ]
    return result


def _governance(before: object, after: object, *, write_status: int) -> dict:
    return {
        "accepted": True,
        "before": {"status": 200, "body": before},
        "write": {"status": write_status, "body": {}},
        "after": {"status": 200, "body": after},
        "audit_path": "audit.jsonl",
        "audit_record": {
            "phase": "write" if write_status == 201 else "cleanup",
            "status": write_status,
        },
    }


def _execution_rows(*, dirty_payment: bool = False, missing_payment_after: bool = False) -> list[dict]:
    order_before = {"orders": []}
    order_after = {"orders": [{"order_id": "ORD-1"}]}
    payment_before = {"payments": []}
    payment_after = {"payments": [{"payment_id": "PAY-1", "order_id": "ORD-1"}]}
    payment_cleanup_after = (
        {"payments": [{"payment_id": "PAY-1", "order_id": "ORD-1"}]}
        if dirty_payment
        else payment_before
    )
    payment_cleanup_governance = _governance(
        payment_after,
        payment_cleanup_after,
        write_status=204,
    )
    if missing_payment_after:
        payment_cleanup_governance.pop("after", None)
    return [
        {
            "phase": "treatment",
            "step_id": "create-order",
            "operation_ref": "op-create-order",
            "system_ref": "orders",
            "method": "POST",
            "path": "/orders",
            "status_code": 201,
            "body": {"order_id": "ORD-1"},
            "governance_receipt": _governance(
                order_before,
                order_after,
                write_status=201,
            ),
        },
        {
            "phase": "treatment",
            "step_id": "create-payment",
            "operation_ref": "op-create-payment",
            "system_ref": "payments",
            "method": "POST",
            "path": "/payments",
            "status_code": 201,
            "body": {"payment_id": "PAY-1", "order_id": "ORD-1"},
            "governance_receipt": _governance(
                payment_before,
                payment_after,
                write_status=201,
            ),
        },
        {
            "phase": "cleanup",
            "step_id": "cleanup_create-payment",
            "compensates_step_id": "create-payment",
            "operation_ref": "op-delete-payment",
            "system_ref": "payments",
            "method": "DELETE",
            "path": "/payments/PAY-1",
            "observation_path": "/payments",
            "status_code": 204,
            "governance_receipt": payment_cleanup_governance,
        },
        {
            "phase": "cleanup",
            "step_id": "cleanup_create-order",
            "compensates_step_id": "create-order",
            "operation_ref": "op-delete-order",
            "system_ref": "orders",
            "method": "DELETE",
            "path": "/orders/ORD-1",
            "observation_path": "/orders",
            "status_code": 204,
            "governance_receipt": _governance(
                order_after,
                order_before,
                write_status=204,
            ),
        },
    ]


def _equivalence_result(
    *,
    dirty_payment: bool = False,
    missing_payment_after: bool = False,
) -> tuple[dict, dict]:
    exp = _compiled()
    prepared = finalize_process_graph_cleanup_equivalence_inputs(
        exp=exp,
        result={
            "steps_out": _execution_rows(
                dirty_payment=dirty_payment,
                missing_payment_after=missing_payment_after,
            ),
            "observations": {},
            "cleanup_failures": 0,
        },
        resolved_campaign_id="campaign",
        runtime_bindings={
            "order_id": "ORD-1",
            "payment_id": "PAY-1",
        },
    )
    observations = prepared["observations"]
    receipt = evaluate_cleanup_equivalence(
        proof=exp["write_reversibility_proof"],
        before_observation={},
        after_write_observation={},
        after_cleanup_observation={},
        runtime_bindings={},
        cleanup_execution_receipt=observations[
            "cleanup_execution_receipt"
        ],
    )
    return receipt, observations


def test_multi_write_graph_compiles_with_one_proof_per_write() -> None:
    result = _compiled()
    proof = result["write_reversibility_proof"]

    assert proof["schema_version"] == GRAPH_REVERSIBILITY_SCHEMA
    assert proof["proof_status"] == "PROVEN"
    assert proof["write_step_ids"] == ["create-order", "create-payment"]
    assert proof["cleanup_order"] == ["create-payment", "create-order"]
    assert set(proof["step_proofs_by_id"]) == {
        "create-order",
        "create-payment",
    }
    assert all(
        node_proof["proof_status"] == "PROVEN"
        for node_proof in proof["step_proofs_by_id"].values()
    )
    assert result["compile_receipt"][
        "graph_step_reversibility_proof_count"
    ] == 2


def test_runtime_revalidates_every_graph_write_proof() -> None:
    result = _compiled()
    validation = validate_process_graph_reversibility_runtime(
        result,
        _behavior_ir(),
        compile_proof_fingerprint=result["write_reversibility_proof"][
            "fingerprint"
        ],
        runtime_bindings={
            "order_id": "ORD-1",
            "payment_id": "PAY-1",
        },
        binding_receipts=[],
    )
    assert validation["valid"] is True, validation
    assert set(validation["runtime_step_proofs_by_id"]) == {
        "create-order",
        "create-payment",
    }

    tampered = deepcopy(result)
    tampered["write_reversibility_proof"]["step_proofs_by_id"][
        "create-payment"
    ]["proof_id"] = "tampered"
    invalid = validate_process_graph_reversibility_runtime(
        tampered,
        _behavior_ir(),
        compile_proof_fingerprint=result["write_reversibility_proof"][
            "fingerprint"
        ],
        runtime_bindings={},
        binding_receipts=[],
    )
    assert invalid["valid"] is False
    assert invalid["reason_code"] == "BLOCKED_CLEANUP_CONTRACT_DRIFT"


def test_all_write_steps_must_be_equivalent_before_environment_restored() -> None:
    receipt, observations = _equivalence_result()

    assert receipt["equivalence_status"] == "EQUIVALENT", receipt
    assert receipt["equivalent_step_count"] == 2
    assert set(receipt["step_equivalence_receipts_by_id"]) == {
        "create-order",
        "create-payment",
    }
    environment = observations["environment_restoration_receipt"]
    assert environment["environment_restored"] is True
    assert environment["final_status"] == "ENVIRONMENT_RESTORED"
    assert len(
        observations["process_graph_step_cleanup_execution_receipts"]
    ) == 2
    assert len(observations["cleanup_execution_receipts"]) == 1


def test_one_dirty_write_makes_the_whole_graph_not_equivalent() -> None:
    receipt, observations = _equivalence_result(dirty_payment=True)

    assert receipt["equivalence_status"] == "NOT_EQUIVALENT"
    assert receipt["not_equivalent_step_count"] == 1
    assert receipt["step_equivalence_receipts_by_id"]["create-order"][
        "equivalence_status"
    ] == "EQUIVALENT"
    assert receipt["step_equivalence_receipts_by_id"]["create-payment"][
        "equivalence_status"
    ] == "NOT_EQUIVALENT"
    environment = observations["environment_restoration_receipt"]
    assert environment["environment_restored"] is False
    assert environment["final_status"] == "ENVIRONMENT_DIRTY"


def test_missing_one_step_after_cleanup_keeps_graph_indeterminate() -> None:
    receipt, observations = _equivalence_result(missing_payment_after=True)

    assert receipt["equivalence_status"] == "INDETERMINATE"
    assert receipt["indeterminate_step_count"] == 1
    assert "create-payment:AFTER_CLEANUP_OBSERVATION_MISSING" in receipt[
        "detail"
    ]
    environment = observations["environment_restoration_receipt"]
    assert environment["environment_restored"] is False
    assert environment["final_status"] == "CLEANUP_FAILED"


def test_cleanup_order_drift_blocks_graph_proof() -> None:
    compiled = _compiled()
    drifted = deepcopy(compiled)
    drifted["process_graph_write_contract"]["cleanup_order"] = [
        "create-order",
        "create-payment",
    ]

    result = finalize_process_graph_reversibility(
        drifted,
        _behavior_ir(),
    )
    assert result["compile_receipt"]["status"] == "BLOCKED"
    assert result["compile_receipt"]["reason_code"] == (
        "BLOCKED_CLEANUP_CONTRACT_DRIFT"
    )
    assert "graph_cleanup_order_not_reverse_write_order" in result[
        "compile_receipt"
    ]["detail"]
