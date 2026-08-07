"""Regression: the Delivery Gate v2 chain must accept a DELIVERABLE finding
whose cleanup adjudication is RESIDUE_ACCEPTED (accepted-residue degradation
for declared non-production targets).

E2E regression (2026-08-07): ``_cleanup_gate_decision`` returns
``("DELIVERABLE", [], "RESIDUE_ACCEPTED")`` for residue-accepted cleanup
contracts, but ``validate_customer_delivery_gate_receipt_v2`` only allowed
``COMPLETED`` / ``NOT_REQUIRED`` cleanup adjudications on the DELIVERABLE
path — so the first write-path experiment reaching the gate crashed the whole
pipeline with ``deliverable_gate_adjudication_invalid``. This test pins the
full build → validate chain for the residue path.
"""
from __future__ import annotations

import hashlib
import json

import pytest

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    evaluate_contract_oracle,
)
from ai_test_asset_center.customer_delivery_gate_v2 import (
    DeliveryGateV2Error,
    build_customer_delivery_gate_receipt_v2,
    build_delivery_execution_receipt,
    build_reproduction_receipt,
    validate_customer_delivery_gate_receipt_v2,
)
from ai_test_asset_center.discovery_mainline_contract import (
    build_mainline_run_contract,
)
from ai_test_asset_center.observer_contracts import build_observer_receipt
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt_from_counts,
)


def _request_semantics_fingerprint(*, phase: str) -> str:
    payload = {
        "operation_ref": "create-thing",
        "method": "POST",
        "path_template": "/api/things",
        "mutation_class": (
            "positive_control" if phase == "control" else "actor_relation_treatment"
        ),
        "mutation_selector": "",
        "mutation_operator": "",
        "request_body_fingerprint": "c" * 64,
    }
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


@pytest.fixture()
def residue_gate_bundle() -> dict:
    return _build_bundle(cleanup_status="RESIDUE_ACCEPTED")


def _build_bundle(*, cleanup_status: str) -> dict:
    mainline = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="run-residue",
        campaign_id="CMP_residue_test",
        target_id="target-1",
        environment_id="environment-1",
        policy_version="v1.0.0-test",
        evaluation_mode="operational",
    )
    suffix = "residue"
    obligation_id = f"obligation-{suffix}"
    experiment_id = f"experiment-{suffix}"
    execution_id = f"execution-{suffix}"
    candidate_id = f"candidate-{suffix}"
    slice_id = f"slice-{suffix}"
    evidence_id = f"evidence-{suffix}"

    if cleanup_status == "RESIDUE_ACCEPTED":
        cleanup_evidence = {
            "residue": True,
            "cleanup_write_count": 0,
            "accepted_write_count": 1,
            "residue_notice": "no_source_compensator:POST /api/things",
        }
    elif cleanup_status == "COMPLETED":
        cleanup_evidence = {
            "accepted_write_count": 1,
            "cleanup_write_count": 1,
            "restoration_verified": True,
            "state_unchanged": True,
            "audit_receipt_ids": ["audit-cleanup-1"],
        }
    else:
        raise AssertionError(f"unsupported cleanup status: {cleanup_status}")

    experiment = {
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "campaign_id": "CMP_residue_test",
        "execution_id": execution_id,
        "source_refs": [{
            "kind": "api_contract",
            "source_id": "src-1",
            "locator": "POST /api/things",
        }],
        "control_plan": [{
            "step_id": f"control-{suffix}",
            "actor_ref": "owner",
            "operation_ref": "create-thing",
        }],
        "treatment_plan": [{
            "step_id": f"treatment-{suffix}",
            "actor_ref": "viewer",
            "operation_ref": "create-thing",
        }],
        "fixture_dag": {"nodes": [], "setup_order": []},
        "observers": [{"observer_id": "http_response"}],
        "cleanup_plan": [],
        "assertions": [{
            "assertion_id": f"assert-status-{suffix}",
            "kind": "http_status",
            "expected": 403,
        }],
    }
    contract_receipts = [
        build_contract_evidence_receipt(
            kind=kind,
            experiment_id=experiment_id,
            obligation_id=obligation_id,
            campaign_id="CMP_residue_test",
            execution_id=execution_id,
            subject_id=subject_id,
            status=status,
            evidence=evidence,
        )
        for kind, subject_id, status, evidence in (
            (
                "control",
                f"control-{suffix}",
                "OBSERVED",
                {
                    "response_observed": True,
                    "status_code": 200,
                    "control_succeeded": True,
                    "path_template": "/api/things",
                    "request_body_fingerprint": "c" * 64,
                    "request_semantics_fingerprint": _request_semantics_fingerprint(
                        phase="control"
                    ),
                    "mutation_class": "positive_control",
                    "mutation_selector": "",
                    "mutation_operator": "",
                },
            ),
            (
                "treatment",
                f"treatment-{suffix}",
                "OBSERVED",
                {
                    "response_observed": True,
                    "status_code": 200,
                    "path_template": "/api/things",
                    "request_body_fingerprint": "c" * 64,
                    "request_semantics_fingerprint": _request_semantics_fingerprint(
                        phase="treatment"
                    ),
                    "mutation_class": "actor_relation_treatment",
                    "mutation_selector": "",
                    "mutation_operator": "",
                },
            ),
            ("actor", "owner", "OBSERVED", {"role": "buyer"}),
            ("actor", "viewer", "OBSERVED", {"role": "buyer"}),
            (
                "cleanup",
                f"cleanup-{suffix}",
                cleanup_status,
                cleanup_evidence,
            ),
        )
    ]
    observer = build_observer_receipt(
        observer_id="http_response",
        status="OBSERVED",
        campaign_id="CMP_residue_test",
        execution_id=execution_id,
        evidence={"statuses": [200, 200]},
    )
    oracle = evaluate_contract_oracle(
        experiment=experiment,
        evidence={
            "campaign_id": "CMP_residue_test",
            "execution_id": execution_id,
            "status_code": 200,
            "contract_evidence_receipts": contract_receipts,
            "observer_receipts": [observer],
        },
    )
    assert oracle.get("status") == "VIOLATION"

    steps = []
    for phase in ("control", "treatment"):
        steps.append({
            "phase": phase,
            "step_id": f"{phase}-{suffix}",
            "actor_ref": "owner" if phase == "control" else "viewer",
            "operation_ref": "create-thing",
            "method": "POST",
            "path": "/api/things",
            "status_code": 200,
            "body": {"name": "thing-1"},
            "observation_receipt_id": f"observation-{phase}-{suffix}",
            "path_template": "/api/things",
            "request_body_fingerprint": "c" * 64,
            "request_semantics_fingerprint": _request_semantics_fingerprint(phase=phase),
            "mutation_class": (
                "positive_control" if phase == "control"
                else "actor_relation_treatment"
            ),
            "mutation_selector": "",
            "mutation_operator": "",
        })
    operational_cleanup_status = (
        "COMPLETED" if cleanup_status == "COMPLETED" else "NOT_REQUIRED"
    )
    operational = build_execution_operational_receipt_from_counts(
        receipt_id=f"operational-{suffix}",
        execution_status="EXECUTED",
        scenario_attempt_count=1,
        http_request_attempt_count=2,
        write_request_attempt_count=2,
        production_http_request_count=0,
        accepted_non_cleanup_write_count=1,
        accepted_cleanup_write_count=0,
        cleanup_status=operational_cleanup_status,
        cleanup_attempted_count=(
            1 if cleanup_status == "COMPLETED" else 0
        ),
        cleanup_completed_count=(
            1 if cleanup_status == "COMPLETED" else 0
        ),
        cleanup_failure_count=0,
    )
    delivery_execution = build_delivery_execution_receipt(
        mainline_run=mainline,
        candidate_id=candidate_id,
        slice_id=slice_id,
        obligation_id=obligation_id,
        experiment_id=experiment_id,
        execution_id=execution_id,
        evidence_id=evidence_id,
        operational_receipt=operational,
        observation_receipt_ids=[
            f"observation-control-{suffix}",
            f"observation-treatment-{suffix}",
            observer["receipt_id"],
            *[receipt["receipt_id"] for receipt in contract_receipts],
        ],
        oracle_receipt_id=oracle["receipt_id"],
    )
    reproduction = build_reproduction_receipt(
        execution_receipt=delivery_execution,
        steps=steps,
        oracle_receipt=oracle,
        source_refs=experiment["source_refs"],
    )
    finding = {
        "finding_id": f"finding-{suffix}",
        "candidate_id": candidate_id,
        "slice_id": slice_id,
        "obligation_id": obligation_id,
        "experiment_id": experiment_id,
        "execution_id": execution_id,
        "evidence_id": evidence_id,
        "campaign_id": "CMP_residue_test",
        "mainline_run": {"contract_fingerprint": mainline["contract_fingerprint"]},
        "title": "viewer can create thing",
        "evidence": {"requests": [], "responses": []},
    }
    return {
        "finding": finding,
        "execution_receipt": delivery_execution,
        "contract_evidence_receipts": contract_receipts,
        "observer_receipts": [observer],
        "oracle_receipt": oracle,
        "reproduction_receipt": reproduction,
    }


def test_residue_accepted_cleanup_delivers_and_validates(
    residue_gate_bundle: dict,
) -> None:
    gate = build_customer_delivery_gate_receipt_v2(**residue_gate_bundle)

    assert gate.get("status") == "DELIVERABLE"
    assert gate.get("reason_codes") == []
    assert gate.get("adjudication", {}).get("cleanup") == "RESIDUE_ACCEPTED"

    # The public validator is the exact crash site of the E2E run: it must
    # accept the residue adjudication instead of raising
    # deliverable_gate_adjudication_invalid.
    validated = validate_customer_delivery_gate_receipt_v2(
        gate,
        finding=residue_gate_bundle["finding"],
    )
    assert validated["adjudication"]["cleanup"] == "RESIDUE_ACCEPTED"
    assert validated["status"] == "DELIVERABLE"


def test_completed_cleanup_still_delivers() -> None:
    """The COMPLETED path must keep working (no regression)."""
    bundle = _build_bundle(cleanup_status="COMPLETED")

    gate = build_customer_delivery_gate_receipt_v2(**bundle)
    assert gate.get("status") == "DELIVERABLE"
    assert gate.get("adjudication", {}).get("cleanup") == "COMPLETED"
