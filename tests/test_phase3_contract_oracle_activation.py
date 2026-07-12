from __future__ import annotations

import copy
import hashlib
import json

import pytest

from ai_test_asset_center.contract_oracles import (
    ACTIVATION_RECEIPT_SCHEMA,
    CONTRACT_ORACLE_RECEIPT_SCHEMA,
    build_contract_evidence_receipt,
    build_contract_oracle_activation_receipt,
    evaluate_contract_oracle,
    scenario_has_contract_activation,
    validate_contract_oracle_activation_receipt,
    validate_contract_oracle_receipt,
)
from ai_test_asset_center.observer_contracts import build_observer_receipt


SOURCE_REFS = [
    {
        "kind": "permission_matrix",
        "source_id": "roles",
        "locator": "owner->viewer:order.read:DENY",
    }
]
CAMPAIGN_ID = "campaign-1"
EXECUTION_ID = "execution-1"


def _experiment() -> dict:
    return {
        "experiment_id": "exp-1",
        "obligation_id": "obl-1",
        "campaign_id": CAMPAIGN_ID,
        "execution_id": EXECUTION_ID,
        "source_refs": SOURCE_REFS,
        "control_plan": [
            {"step_id": "control-1", "actor_ref": "owner", "operation_ref": "get-order"}
        ],
        "treatment_plan": [
            {"step_id": "treatment-1", "actor_ref": "viewer", "operation_ref": "get-order"}
        ],
        "fixture_dag": {
            "setup_order": ["fixture-order"],
            "nodes": [
                {"node_id": "fixture-order", "kind": "runtime_read_binding"}
            ],
        },
        "observers": [
            {
                "observer_id": "authorization_comparison",
                "receipt_schema": "qualibug.observer-receipt.v1",
                "required_status": "OBSERVED",
            }
        ],
        "cleanup_plan": [
            {"step_id": "cleanup-1", "operation_ref": "delete-order"}
        ],
        "assertions": [
            {
                "assertion_id": "assert-auth",
                "kind": "owner_tenant_visibility",
            }
        ],
    }


def _contract_receipt(kind: str, subject_id: str, status: str) -> dict:
    evidence = {"source": "test_execution"}
    if kind == "control":
        evidence.update({
            "response_observed": True,
            "status_code": 200,
            "control_succeeded": True,
        })
    elif kind == "treatment":
        evidence.update({"response_observed": True, "status_code": 200})
    elif kind == "cleanup":
        evidence.update({
            "accepted_write_count": 1,
            "cleanup_write_count": 1,
            "restoration_verified": True,
            "state_unchanged": True,
            "audit_receipt_ids": ["audit-1"],
        })
    return build_contract_evidence_receipt(
        kind=kind,
        experiment_id="exp-1",
        obligation_id="obl-1",
        campaign_id=CAMPAIGN_ID,
        execution_id=EXECUTION_ID,
        subject_id=subject_id,
        status=status,
        evidence=evidence,
    )


def _evidence() -> dict:
    observer = build_observer_receipt(
        observer_id="authorization_comparison",
        status="OBSERVED",
        campaign_id=CAMPAIGN_ID,
        execution_id=EXECUTION_ID,
        evidence={
            "owner_can_access": True,
            "viewer_can_access": True,
            "leak_detected": True,
            "same_resource_proven": True,
        },
    )
    return {
        "contract_evidence_receipts": [
            _contract_receipt("control", "control-1", "OBSERVED"),
            _contract_receipt("treatment", "treatment-1", "OBSERVED"),
            _contract_receipt("actor", "owner", "OBSERVED"),
            _contract_receipt("actor", "viewer", "OBSERVED"),
            _contract_receipt("fixture", "fixture-order", "OBSERVED"),
            _contract_receipt("cleanup", "cleanup-1", "COMPLETED"),
        ],
        "observer_receipts": [observer],
        "control_succeeded": True,
        "owner_can_access": True,
        "viewer_can_access": True,
        "leak_detected": True,
        "same_resource_proven": True,
    }


def test_complete_typed_receipt_chain_activates_and_emits_violation() -> None:
    experiment = _experiment()
    evidence = _evidence()
    activation = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )
    oracle = evaluate_contract_oracle(experiment=experiment, evidence=evidence)

    assert activation["schema_version"] == ACTIVATION_RECEIPT_SCHEMA
    assert activation["status"] == "ACTIVE"
    assert not activation["reason_codes"]
    assert validate_contract_oracle_activation_receipt(activation) == activation
    assert oracle["schema_version"] == CONTRACT_ORACLE_RECEIPT_SCHEMA
    assert oracle["status"] == "VIOLATION"
    assert oracle["verdict"] == "customer_deliverable_defect_candidate"
    assert oracle["customer_deliverable"] is False
    assert oracle["customer_deliverable_candidate"] is True
    assert len(oracle["failed_assertions"]) == 1
    assert oracle["failed_assertions"][0]["status"] == "VIOLATION"
    assert oracle["campaign_id"] == CAMPAIGN_ID
    assert oracle["execution_id"] == EXECUTION_ID
    assert validate_contract_oracle_receipt(oracle) == oracle


@pytest.mark.parametrize(
    ("plan_key", "reason_code"),
    [
        ("control_plan", "CONTROL_PLAN_MISSING"),
        ("treatment_plan", "TREATMENT_PLAN_MISSING"),
    ],
)
def test_empty_control_or_treatment_plan_cannot_emit_violation(
    plan_key: str,
    reason_code: str,
) -> None:
    experiment = _experiment()
    experiment[plan_key] = []

    oracle = evaluate_contract_oracle(
        experiment=experiment,
        evidence=_evidence(),
    )

    assert oracle["status"] == "BLOCKED"
    assert oracle["customer_deliverable_candidate"] is False
    assert reason_code in oracle["missing_requirements"]


@pytest.mark.parametrize(
    ("kind", "subject_id"),
    [
        ("control", "control-1"),
        ("treatment", "treatment-1"),
        ("actor", "owner"),
        ("fixture", "fixture-order"),
        ("cleanup", "cleanup-1"),
    ],
)
def test_missing_required_receipt_blocks_oracle(kind: str, subject_id: str) -> None:
    experiment = _experiment()
    evidence = _evidence()
    evidence["contract_evidence_receipts"] = [
        receipt
        for receipt in evidence["contract_evidence_receipts"]
        if not (
            receipt["kind"] == kind
            and receipt["subject_id"] == subject_id
        )
    ]

    activation = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )
    oracle = evaluate_contract_oracle(experiment=experiment, evidence=evidence)

    assert activation["status"] == "BLOCKED"
    assert any(code.startswith(f"MISSING_{kind.upper()}_RECEIPT") for code in activation["reason_codes"])
    assert oracle["status"] == "BLOCKED"
    assert oracle["customer_deliverable_candidate"] is False


def test_boolean_flags_and_observer_ids_cannot_spoof_activation() -> None:
    activation = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence={
            "control_succeeded": True,
            "treatment_observation": {"status_code": 200},
            "observer_ids": ["authorization_comparison"],
            "authorization_comparison": True,
        },
    )

    assert activation["status"] == "BLOCKED"
    assert "MISSING_CONTROL_RECEIPT:control-1" in activation["reason_codes"]
    assert "MISSING_OBSERVER_RECEIPT:authorization_comparison" in activation["reason_codes"]
    assert scenario_has_contract_activation({"contract_oracle_enabled": True}) is False


def test_failed_cleanup_and_tampered_receipts_are_harness_failures() -> None:
    experiment = _experiment()
    evidence = _evidence()
    evidence["contract_evidence_receipts"] = [
        (
            _contract_receipt("cleanup", "cleanup-1", "FAILED")
            if receipt["kind"] == "cleanup"
            else receipt
        )
        for receipt in evidence["contract_evidence_receipts"]
    ]
    failed = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )
    assert failed["status"] == "HARNESS_FAILED"
    assert "CLEANUP_RECEIPT_FAILED:cleanup-1" in failed["reason_codes"]

    tampered_evidence = _evidence()
    tampered_evidence["contract_evidence_receipts"][0]["status"] = "FAILED"
    tampered = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=tampered_evidence,
    )
    assert tampered["status"] == "HARNESS_FAILED"
    assert any("CONTRACT_EVIDENCE_RECEIPT_INVALID" in code for code in tampered["reason_codes"])


def test_cleanup_requires_restoration_proof_not_only_completed_status() -> None:
    experiment = _experiment()
    evidence = _evidence()
    evidence["contract_evidence_receipts"] = [
        (
            build_contract_evidence_receipt(
                kind="cleanup",
                experiment_id="exp-1",
                obligation_id="obl-1",
                campaign_id=CAMPAIGN_ID,
                execution_id=EXECUTION_ID,
                subject_id="cleanup-1",
                status="COMPLETED",
                evidence={"status_code": 204},
            )
            if receipt["kind"] == "cleanup"
            else receipt
        )
        for receipt in evidence["contract_evidence_receipts"]
    ]

    activation = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )

    assert activation["status"] == "BLOCKED"
    assert "CLEANUP_RESTORATION_NOT_PROVEN:cleanup-1" in activation["reason_codes"]


def test_not_required_cleanup_is_valid_only_when_no_write_was_accepted() -> None:
    experiment = _experiment()
    evidence = _evidence()
    evidence["contract_evidence_receipts"] = [
        (
            build_contract_evidence_receipt(
                kind="cleanup",
                experiment_id="exp-1",
                obligation_id="obl-1",
                campaign_id=CAMPAIGN_ID,
                execution_id=EXECUTION_ID,
                subject_id="cleanup-1",
                status="NOT_REQUIRED",
                evidence={
                    "accepted_write_count": 0,
                    "cleanup_write_count": 0,
                    "state_unchanged": True,
                    "audit_receipt_ids": ["audit-rejected-write"],
                },
            )
            if receipt["kind"] == "cleanup"
            else receipt
        )
        for receipt in evidence["contract_evidence_receipts"]
    ]

    activation = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )

    assert activation["status"] == "ACTIVE"


def test_cross_execution_receipts_cannot_activate_oracle() -> None:
    evidence = _evidence()
    evidence["contract_evidence_receipts"][0] = build_contract_evidence_receipt(
        kind="control",
        experiment_id="exp-1",
        obligation_id="obl-1",
        campaign_id=CAMPAIGN_ID,
        execution_id="execution-other",
        subject_id="control-1",
        status="OBSERVED",
        evidence={
            "response_observed": True,
            "status_code": 200,
            "control_succeeded": True,
        },
    )

    activation = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence=evidence,
    )

    assert activation["status"] == "HARNESS_FAILED"
    assert any("CONTRACT_EVIDENCE_LINEAGE_MISMATCH" in code for code in activation["reason_codes"])


def test_oracle_validator_rejects_semantically_inconsistent_refingerprinted_receipt() -> None:
    oracle = evaluate_contract_oracle(
        experiment=_experiment(),
        evidence=_evidence(),
    )
    tampered = copy.deepcopy(oracle)
    tampered["status"] = "PROPERTY_HELD"
    payload = {key: value for key, value in tampered.items() if key != "receipt_id"}
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    tampered["receipt_id"] = "oracle_" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()[:24]

    with pytest.raises(ValueError, match="contract_oracle_semantics_invalid"):
        validate_contract_oracle_receipt(tampered)


def test_activation_receipt_is_deterministic_and_tamper_evident() -> None:
    first = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence=_evidence(),
    )
    second = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence=_evidence(),
    )
    assert first == second

    tampered = copy.deepcopy(first)
    tampered["status"] = "BLOCKED"
    with pytest.raises(
        ValueError,
        match="activation_receipt_(semantics|fingerprint)_invalid",
    ):
        validate_contract_oracle_activation_receipt(tampered)


def test_embedded_activation_without_recomputable_evidence_cannot_activate() -> None:
    experiment = _experiment()
    evidence = _evidence()
    activation = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )

    assert scenario_has_contract_activation({
        "activation_receipt": activation,
    }) is False
    assert scenario_has_contract_activation({
        "activation_receipt": activation,
        "experiment": experiment,
        "evidence": evidence,
    }) is True
