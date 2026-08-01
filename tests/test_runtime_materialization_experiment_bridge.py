from __future__ import annotations

import json

from ai_test_asset_center import experiment_compiler
from ai_test_asset_center import experiment_outcome_finalizer
from ai_test_asset_center import experiment_runtime_support
from ai_test_asset_center.runtime_materialization_experiment_bridge import (
    attach_materialization_lineage_to_result,
    bind_experiment_pack_to_captured_materializations,
    capture_enterprise_runtime_materializations,
    validate_experiment_materialization_contract,
)
from ai_test_asset_center.runtime_materialization_operation_matching import (
    install_runtime_materialization_operation_matching,
)


PROJECT = "customer_materialization_bridge"


def _behavior_ir() -> dict:
    return {
        "project_id": PROJECT,
        "model_id": "bir_customer_materialization_bridge",
        "operations": [
            {
                "id": "bir_op_ship_order",
                "operation_id": "shipOrder",
                "source_operation_refs": [
                    "shipOrder",
                    "api:POST:/orders/{order_id}/ship",
                ],
                "method": "POST",
                "path": "/orders/{order_id}/ship",
            },
            {
                "id": "bir_op_cancel_order",
                "operation_id": "cancelOrder",
                "source_operation_refs": ["cancelOrder"],
                "method": "POST",
                "path": "/orders/{order_id}/cancel",
            },
        ],
    }


def _materialization(
    materialization_id: str = "runtime_materialization_ship_order",
    *,
    actor_ref: str = "warehouse_operator",
) -> dict:
    return {
        "materialization_id": materialization_id,
        "runtime_plan_ref": "runtime_plan_ship_order",
        "execution_contract_ref": "execution_contract_ship_order",
        "scenario_ref": "scenario_ir_ship_order",
        "status": "DRAFT_READY",
        "formal_runtime_materialization": True,
        "execution_allowed": False,
        "request_sendable": False,
        "network_calls_allowed": False,
        "environment_binding": {
            "environment_ref": "env:sit",
            "environment_kind": "SIT",
            "base_url": "https://sit.example.internal",
            "non_production_proven": True,
        },
        "credential_binding": {
            "credential_slots": [
                {
                    "actor_ref": actor_ref,
                    "credential_ref": "secret_ref:test_accounts:warehouse_operator",
                    "secret_value_loaded": False,
                }
            ],
            "secret_values_loaded": False,
        },
        "request_value_bindings": [
            {
                "slot_id": "slot:order_id",
                "field": "order_id",
                "location": "PATH",
                "binding_kind": "APPROVED_RUNTIME_LITERAL",
                "draft_value": "ORD-1001",
                "draft_value_present": True,
                "resolution_status": "RESOLVED_RUNTIME_LITERAL",
            }
        ],
        "request_draft": {
            "method": "POST",
            "interface_id": "api:POST:/orders/{order_id}/ship",
            "operation_id": "shipOrder",
            "path_draft": "/orders/ORD-1001/ship",
            "url_draft": "https://sit.example.internal/orders/ORD-1001/ship",
            "draft_compiled": True,
            "request_sendable": False,
        },
        "assertion_drafts": [
            {
                "draft_id": "assertion:ship-order",
                "draft_kind": "HTTP_RESPONSE_SEMANTIC_ASSERTION_DRAFT",
                "assertion_executable": False,
            }
        ],
        "cleanup_draft": {
            "write_action": True,
            "cleanup_binding_resolved": True,
            "cleanup_executable": False,
        },
    }


def _asset(*materializations: dict, gate_passed: bool = True) -> dict:
    return {
        "asset_id": "knowledge_asset_customer_materialization_bridge",
        "runtime_materializations": list(materializations),
        "runtime_materialization_unknowns": [],
        "runtime_materialization_gate": {
            "status": "PASS" if gate_passed else "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE",
            "entry_allowed": gate_passed,
            "runtime_materialization_ready": gate_passed,
            "execution_allowed": False,
        },
        "governance": {
            "legacy_probe_generation_requires_runtime_materialization_gate": True,
        },
    }


def _experiment() -> dict:
    return {
        "experiment_id": "experiment_ship_order",
        "obligation_id": "obligation_ship_order",
        "compile_receipt": {"status": "COMPILED"},
        "actor_ref": "warehouse_operator",
        "treatment_plan": [
            {
                "step_id": "step:ship-order",
                "operation_ref": "bir_op_ship_order",
            }
        ],
    }


def _pack(experiment: dict | None = None) -> dict:
    return {
        "schema_version": "qualibug.experiment-compile.v1",
        "compiled_count": 1,
        "blocked_count": 0,
        "experiments": [experiment or _experiment()],
        "blocked_experiments": [],
        "block_reason_counts": {},
    }


def _bind(*materializations: dict, gate_passed: bool = True) -> dict:
    install_runtime_materialization_operation_matching()
    capture_enterprise_runtime_materializations(
        PROJECT,
        _asset(*materializations, gate_passed=gate_passed),
    )
    return bind_experiment_pack_to_captured_materializations(
        _pack(),
        behavior_ir=_behavior_ir(),
        obligations=[{"obligation_id": "obligation_ship_order", "compile_status": "COMPILED"}],
    )


def test_unique_source_operation_materialization_binds_existing_experiment() -> None:
    result = _bind(_materialization())

    assert result["compiled_count"] == 1
    assert result["blocked_count"] == 0
    assert result["runtime_materialization_bridge"]["status"] == "PASS"
    experiment = result["experiments"][0]
    contract = experiment["runtime_materialization_contract"]

    assert contract["materialization_id"] == "runtime_materialization_ship_order"
    assert contract["runtime_plan_id"] == "runtime_plan_ship_order"
    assert contract["scenario_ir_id"] == "scenario_ir_ship_order"
    assert contract["execution_contract_id"] == "execution_contract_ship_order"
    assert contract["authority_fingerprint"]
    assert experiment["compile_receipt"]["runtime_materialization_bridge_status"] == "BOUND"

    # Concrete approved values and raw target addresses are not copied into the frozen authority.
    serialized = json.dumps(contract, ensure_ascii=False, sort_keys=True)
    assert "ORD-1001" not in serialized
    assert "https://sit.example.internal" not in serialized
    assert "secret_ref:test_accounts:warehouse_operator" in serialized


def test_compiler_facade_invokes_materialization_bridge(monkeypatch) -> None:
    capture_enterprise_runtime_materializations(PROJECT, _asset(_materialization()))
    monkeypatch.setattr(
        experiment_compiler._base._base,
        "compile_experiments",
        lambda *args, **kwargs: _pack(),
    )
    obligations = [{"obligation_id": "obligation_ship_order", "compile_status": "COMPILED"}]

    result = experiment_compiler.compile_experiments(
        obligations,
        behavior_ir=_behavior_ir(),
    )

    assert result["compiled_count"] == 1
    assert result["experiments"][0]["runtime_materialization_bridge_required"] is True
    assert obligations[0]["runtime_materialization_status"] == "BOUND"


def test_ambiguous_materializations_are_blocked_before_execution() -> None:
    result = _bind(
        _materialization("runtime_materialization_ship_order_a"),
        _materialization("runtime_materialization_ship_order_b"),
    )

    assert result["compiled_count"] == 0
    assert result["blocked_count"] == 1
    blocked = result["blocked_experiments"][0]
    assert blocked["compile_receipt"]["reason_code"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_AMBIGUOUS"
    )
    assert result["runtime_materialization_bridge"]["status"] == "BLOCKED"


def test_materialization_gate_blocks_formerly_compiled_experiment() -> None:
    result = _bind(_materialization(), gate_passed=False)

    assert result["compiled_count"] == 0
    assert result["blocked_count"] == 1
    assert result["blocked_experiments"][0]["compile_receipt"]["reason_code"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_GATE"
    )


def test_runtime_preflight_accepts_frozen_contract_and_rejects_fingerprint_drift() -> None:
    experiment = _bind(_materialization())["experiments"][0]

    ok, reason, _detail = validate_experiment_materialization_contract(
        experiment,
        behavior_ir=_behavior_ir(),
    )
    assert ok is True
    assert reason == ""

    experiment["runtime_materialization_contract"]["authority"]["lineage"][
        "runtime_plan_id"
    ] = "runtime_plan_tampered"
    ok, reason, detail = validate_experiment_materialization_contract(
        experiment,
        behavior_ir=_behavior_ir(),
    )
    assert ok is False
    assert reason == "BLOCKED_RUNTIME_MATERIALIZATION_CONTRACT_DRIFT"
    assert detail["expected_fingerprint"] != detail["actual_fingerprint"]


def test_runtime_preflight_rejects_operation_drift() -> None:
    experiment = _bind(_materialization())["experiments"][0]
    experiment["treatment_plan"] = [
        {"step_id": "step:cancel-order", "operation_ref": "bir_op_cancel_order"}
    ]

    ok, reason, detail = validate_experiment_materialization_contract(
        experiment,
        behavior_ir=_behavior_ir(),
    )

    assert ok is False
    assert reason == "BLOCKED_RUNTIME_MATERIALIZATION_OPERATION_DRIFT"
    assert detail["expected_request_identity"]["path"] == "/orders/{}/ship"
    assert detail["actual_request_identity"]["path"] == "/orders/{}/cancel"


def test_existing_runtime_preflight_and_finalizer_are_extended_not_replaced() -> None:
    assert getattr(
        experiment_runtime_support.preflight_experiment_executable,
        "__qualibug_runtime_materialization_preflight_v1__",
        False,
    ) is True
    from ai_test_asset_center import experiment_executor

    assert (
        experiment_executor.finalize_experiment_execution
        is experiment_outcome_finalizer.finalize_experiment_execution
    )
    assert "runtime_materialization_lineage" in (
        experiment_outcome_finalizer.finalizer_hook_names()
    )


def test_execution_and_finding_receipts_keep_one_materialization_lineage() -> None:
    experiment = _bind(_materialization())["experiments"][0]
    result = attach_materialization_lineage_to_result(
        {
            "status": "VIOLATION",
            "execution_receipt": {"status": "EXECUTED"},
            "finding": {"finding_id": "finding_ship_order"},
        },
        experiment=experiment,
    )

    lineage = result["runtime_materialization_lineage"]
    assert lineage["status"] == "BOUND_AND_VERIFIED"
    assert lineage["materialization_id"] == "runtime_materialization_ship_order"
    assert result["execution_receipt"]["runtime_materialization_lineage"] == lineage
    assert result["finding"]["runtime_materialization_lineage"] == lineage
