"""Authorization findings require a complete single-variable causal receipt chain."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from ai_test_asset_center import experiment_executor
from ai_test_asset_center.authorization_oracle_causality import (
    build_authorization_causality_receipt,
    enforce_authorization_oracle_causality,
)
from ai_test_asset_center.contract_oracles import build_contract_evidence_receipt
from ai_test_asset_center.observer_contracts_base import build_observer_receipt


def _actor(actor_id: str, role: str, account_ref: str) -> dict:
    return {
        "actor_id": actor_id,
        "role": role,
        "account_ref": account_ref,
    }


def _behavior_ir() -> dict:
    return {
        "actors": [
            _actor("actor:control", "管理员", "control"),
            _actor("actor:treatment", "访客", "treatment"),
        ]
    }


def _accounts(*, treatment_tenant: str = "tenant-a") -> list[dict]:
    return [
        {
            "account_ref": "control",
            "credential_ref": "secret_ref:test_accounts:control",
            "role": "管理员",
            "tenant_id": "tenant-a",
            "organization_id": "org-a",
            "status": "ACTIVE",
        },
        {
            "account_ref": "treatment",
            "credential_ref": "secret_ref:test_accounts:treatment",
            "role": "访客",
            "tenant_id": treatment_tenant,
            "organization_id": "org-a",
            "status": "ACTIVE",
        },
    ]


def _experiment() -> dict:
    return {
        "experiment_id": "exp:auth",
        "obligation_id": "obl:auth",
        "campaign_id": "campaign:1",
        "execution_id": "execution:1",
        "risk_family": "authorization",
        "compile_receipt": {
            "status": "COMPILED",
            "authorization_comparison_fingerprint": "comparison-fingerprint",
        },
        "actor_selection_contract": {
            "control_actor_ref": "actor:control",
            "treatment_actor_ref": "actor:treatment",
        },
        "control_plan": [
            {
                "step_id": "control_1",
                "actor_ref": "actor:control",
                "operation_ref": "op:get-order",
                "path": "/orders/{order_id}",
            }
        ],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "actor_ref": "actor:treatment",
                "operation_ref": "op:get-order",
                "path": "/orders/{order_id}",
            }
        ],
        "binding_plan": [
            {
                "target": "order_id",
                "status": "runtime_resolvable",
            }
        ],
        "source_identity_fields": ["order_id"],
        "authorization_comparison_contract": {
            "schema_version": "qualibug.authorization-comparison-contract.v1",
            "status": "COMPILED_RUNTIME_VERIFICATION_REQUIRED",
            "risk_family": "authorization",
            "comparison_dimension": "ROLE_PERMISSION",
            "control_actor_ref": "actor:control",
            "treatment_actor_ref": "actor:treatment",
            "control_operation_ref": "op:get-order",
            "treatment_operation_ref": "op:get-order",
            "same_operation_required": True,
            "same_request_baseline_required": True,
            "allowed_request_mutation_paths": [],
            "observed_request_diff_paths": [],
            "invariant_identity_dimensions": [
                "tenant_ref",
                "organization_ref",
                "department_ref",
                "warehouse_ref",
                "project_ref",
                "region_ref",
                "ownership_scope",
            ],
            "allowed_varying_identity_dimensions": [
                "account_ref",
                "role",
                "permission_decision",
            ],
            "resource_identity_binding_targets": ["order_id"],
            "same_resource_identity_required": True,
            "shared_binding_graph_fingerprint": "binding-graph-fingerprint",
            "runtime_identity_verification_required": True,
            "execution_allowed_without_runtime_verification": False,
            "automatic_identity_dimension_substitution_allowed": False,
        },
    }


def _contract_receipt(phase: str, *, target_reached: bool = True) -> dict:
    control = phase == "control"
    return build_contract_evidence_receipt(
        kind=phase,
        experiment_id="exp:auth",
        obligation_id="obl:auth",
        campaign_id="campaign:1",
        execution_id="execution:1",
        subject_id=f"{phase}_1",
        status="OBSERVED",
        evidence={
            "response_observed": target_reached,
            "status_code": 200 if target_reached else 0,
            **({"control_succeeded": target_reached} if control else {}),
        },
    )


def _observer_receipt(*, status: str = "OBSERVED") -> dict:
    return build_observer_receipt(
        observer_id="authorization_comparison",
        status=status,
        reason_code="" if status == "OBSERVED" else "SAME_RESOURCE_NOT_PROVEN",
        campaign_id="campaign:1",
        execution_id="execution:1",
        evidence={
            "owner_can_access": status == "OBSERVED",
            "viewer_can_access": status == "OBSERVED",
            "leak_detected": status == "OBSERVED",
            "same_resource_proven": status == "OBSERVED",
            "resource_match_basis": "identity_overlap" if status == "OBSERVED" else "",
        },
    )


def _sealed_binding_row() -> dict:
    from ai_test_asset_center.binding_materialization_identity_receipt import (
        build_binding_materialization_identity_receipt,
    )

    row = {
        "target": "order_id",
        "status": "BOUND",
        "value_fingerprint": "order-42-fingerprint",
        "source_priority": "same_actor_list_read",
        "resolver_operation_ref": "op:get-order",
        "resolver_path": "/orders/order-42",
        "status_code": 200,
    }
    proof = build_binding_materialization_identity_receipt(row)
    return {
        **row,
        "materialization_receipt_id": proof["receipt_id"],
        "materialization_identity_receipt": proof,
    }


def _result() -> dict:
    return {
        "schema_version": "qualibug.experiment-execution.v1",
        "experiment_id": "exp:auth",
        "obligation_id": "obl:auth",
        "campaign_id": "campaign:1",
        "execution_id": "execution:1",
        "status": "EXECUTED",
        "reason_code": "",
        "detail": "",
        "oracle_verdict": {
            "status": "VIOLATION",
            "verdict": "customer_deliverable_defect_candidate",
            "customer_deliverable_candidate": True,
            "receipt_id": "oracle:1",
            "activation_receipt_id": "activation:1",
        },
        "finding": {
            "title": "authorization leak",
            "oracle": {"oracle_name": "ContractOracle"},
            "evidence": {},
        },
        "contract_evidence_receipts": [
            _contract_receipt("control"),
            _contract_receipt("treatment"),
        ],
        "observer_receipts": [_observer_receipt()],
        "binding_materialization_receipts": [_sealed_binding_row()],
        "execution_receipt": {"status": "EXECUTED"},
    }


def test_complete_causal_chain_preserves_authorization_candidate() -> None:
    output = enforce_authorization_oracle_causality(
        result=_result(),
        experiment=_experiment(),
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )

    receipt = output["authorization_causality_receipt"]
    assert receipt["status"] == "PASSED"
    assert receipt["control_target_reached"] is True
    assert receipt["treatment_target_reached"] is True
    assert receipt["single_identity_dimension_proven"] is True
    assert receipt["same_resource_proven"] is True
    assert receipt["runtime_resource_identity_fingerprint"]
    assert output["finding"] is not None
    assert output["finding"]["oracle"]["authorization_causality_proven"] is True
    assert output["oracle_verdict"]["authorization_causality_gate"] == "PASSED"


def test_control_not_reaching_target_removes_finding() -> None:
    result = _result()
    result["contract_evidence_receipts"][0] = _contract_receipt(
        "control",
        target_reached=False,
    )

    output = enforce_authorization_oracle_causality(
        result=result,
        experiment=_experiment(),
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )

    assert output["finding"] is None
    assert output["reason_code"] == "AUTHORIZATION_CAUSALITY_INDETERMINATE"
    assert output["oracle_verdict"]["status"] == "INDETERMINATE"
    assert output["oracle_verdict"]["customer_deliverable_candidate"] is False
    assert any(
        "CONTROL_TARGET_NOT_REACHED" in reason
        for reason in output["authorization_causality_receipt"]["reason_codes"]
    )


def test_indeterminate_comparison_observer_removes_finding() -> None:
    result = _result()
    result["observer_receipts"] = [_observer_receipt(status="INDETERMINATE")]

    output = enforce_authorization_oracle_causality(
        result=result,
        experiment=_experiment(),
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )

    assert output["finding"] is None
    assert "AUTHORIZATION_CAUSAL_OBSERVER_INDETERMINATE" in (
        output["authorization_causality_receipt"]["reason_codes"]
    )
    assert "AUTHORIZATION_CAUSAL_SAME_RESOURCE_NOT_PROVEN" in (
        output["authorization_causality_receipt"]["reason_codes"]
    )


def test_missing_runtime_resource_binding_removes_finding() -> None:
    result = _result()
    result["binding_materialization_receipts"] = []

    output = enforce_authorization_oracle_causality(
        result=result,
        experiment=_experiment(),
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )

    assert output["finding"] is None
    assert "AUTHORIZATION_CAUSAL_RESOURCE_BINDING_MISSING:order_id" in (
        output["authorization_causality_receipt"]["reason_codes"]
    )


def test_runtime_cross_tenant_role_comparison_removes_finding() -> None:
    output = enforce_authorization_oracle_causality(
        result=_result(),
        experiment=_experiment(),
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(treatment_tenant="tenant-b"),
    )

    assert output["finding"] is None
    assert any(
        reason.startswith("AUTHORIZATION_CAUSAL_IDENTITY_ASYMMETRIC")
        for reason in output["authorization_causality_receipt"]["reason_codes"]
    )


def test_gate_does_not_mutate_input_result() -> None:
    original = _result()
    snapshot = deepcopy(original)

    enforce_authorization_oracle_causality(
        result=original,
        experiment=_experiment(),
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )

    assert original == snapshot


def test_non_comparison_authorization_records_not_applicable_receipt() -> None:
    result = _result()
    experiment = _experiment()
    experiment.pop("authorization_comparison_contract")

    output = enforce_authorization_oracle_causality(
        result=result,
        experiment=experiment,
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )

    receipt = output["authorization_causality_receipt"]
    assert receipt["status"] == "NOT_APPLICABLE"
    assert output["finding"]["authorization_causality_receipt"] == receipt
    assert result["finding"].get("authorization_causality_receipt") is None


def test_public_executor_applies_causal_gate(monkeypatch, tmp_path: Path) -> None:
    from ai_test_asset_center import _experiment_executor_mainline_mechanics

    result = _result()
    result["binding_materialization_receipts"] = []
    experiment = _experiment()

    monkeypatch.setattr(
        _experiment_executor_mainline_mechanics,
        "_execute_one_governed",
        lambda *args, **kwargs: deepcopy(result),
    )
    monkeypatch.setattr(
        experiment_executor._governance,
        "_test_account_rows",
        lambda root, project: _accounts(),
    )

    output = experiment_executor.execute_one_experiment(
        experiment,
        behavior_ir=_behavior_ir(),
        root=tmp_path,
        project="demo",
        base_url="https://test.invalid",
        runtime_contract={},
        campaign_id="campaign:1",
        execution_id="execution:1",
        actor_tokens={},
    )

    assert output["finding"] is None
    assert output["reason_code"] == "AUTHORIZATION_CAUSALITY_INDETERMINATE"
    assert output["authorization_causality_receipt"]["status"] == "INDETERMINATE"


def test_receipt_is_content_addressed() -> None:
    first = build_authorization_causality_receipt(
        result=_result(),
        experiment=_experiment(),
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )
    second = build_authorization_causality_receipt(
        result=_result(),
        experiment=_experiment(),
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )

    assert first == second
    assert first["receipt_id"].startswith("auth_causality_")


def test_collection_operation_uses_observer_resource_identity_proof() -> None:
    experiment = deepcopy(_experiment())
    experiment["binding_plan"] = []
    experiment["source_identity_fields"] = []
    contract = experiment["authorization_comparison_contract"]
    contract["resource_identity_binding_targets"] = []
    contract["same_resource_identity_required"] = True
    result = _result()
    result["binding_materialization_receipts"] = []

    receipt = build_authorization_causality_receipt(
        result=result,
        experiment=experiment,
        behavior_ir=_behavior_ir(),
        account_rows=_accounts(),
    )

    assert receipt["status"] == "PASSED"
    assert len(receipt["runtime_resource_identity_fingerprint"]) == 64
    assert receipt["runtime_resource_identity_fingerprint"] != (
        "observer_same_resource_proven"
    )
