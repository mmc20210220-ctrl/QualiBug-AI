# -*- coding: utf-8 -*-
"""Zero-transport governed steps must not seal CONTRACT_ORACLE_HARNESS_FAILED."""
from __future__ import annotations

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    build_contract_oracle_activation_receipt,
)
from ai_test_asset_center.experiment_outcome_finalizer import (
    _step_scoped_http_response_receipts,
)
from ai_test_asset_center.observer_contracts import build_observer_receipt
from ai_test_asset_center.operational_receipts import (
    build_execution_operational_receipt,
)


def _base_experiment() -> dict:
    return {
        "experiment_id": "exp_zero_transport",
        "obligation_id": "obl_zero_transport",
        "campaign_id": "cmp_zero_transport",
        "execution_id": "exec_zero_transport",
        "risk_family": "authorization",
        "source_refs": [{"source_id": "src_rule", "ref": "rule"}],
        "control_plan": [{"step_id": "control_1", "actor_ref": "actor_admin"}],
        "treatment_plan": [{"step_id": "treatment_1", "actor_ref": "actor_user"}],
        "assertions": [{"kind": "authorization_denied", "property": {}}],
        "observers": [
            {"observer_id": "http_response"},
            {"observer_id": "authorization_comparison"},
        ],
        "cleanup_plan": [],
        "fixture_dag": {"nodes": [], "setup_order": []},
        "binding_plan": [],
    }


def test_zero_transport_failed_control_receipt_blocks_not_harness_failed() -> None:
    exp = _base_experiment()
    control = build_contract_evidence_receipt(
        kind="control",
        experiment_id=exp["experiment_id"],
        obligation_id=exp["obligation_id"],
        campaign_id=exp["campaign_id"],
        execution_id=exp["execution_id"],
        subject_id="control_1",
        status="FAILED",
        evidence={
            "method": "POST",
            "path": "/api/auth/admin/users/u1/status",
            "status_code": 0,
            "response_observed": False,
            "write_reached_transport": False,
            "control_succeeded": False,
        },
    )
    treatment = build_contract_evidence_receipt(
        kind="treatment",
        experiment_id=exp["experiment_id"],
        obligation_id=exp["obligation_id"],
        campaign_id=exp["campaign_id"],
        execution_id=exp["execution_id"],
        subject_id="treatment_1",
        status="FAILED",
        evidence={
            "method": "POST",
            "path": "/api/auth/admin/users/u1/status",
            "status_code": 0,
            "response_observed": False,
            "write_reached_transport": False,
        },
    )
    actor = build_contract_evidence_receipt(
        kind="actor",
        experiment_id=exp["experiment_id"],
        obligation_id=exp["obligation_id"],
        campaign_id=exp["campaign_id"],
        execution_id=exp["execution_id"],
        subject_id="actor_admin",
        status="OBSERVED",
        evidence={"role": "admin", "credential_secret_ref_present": True},
    )
    actor2 = build_contract_evidence_receipt(
        kind="actor",
        experiment_id=exp["experiment_id"],
        obligation_id=exp["obligation_id"],
        campaign_id=exp["campaign_id"],
        execution_id=exp["execution_id"],
        subject_id="actor_user",
        status="OBSERVED",
        evidence={"role": "user", "credential_secret_ref_present": True},
    )
    http_obs = build_observer_receipt(
        observer_id="http_response",
        status="FAILED",
        reason_code="HTTP_RESPONSE_MISSING",
        evidence={
            "step_id": "control_1",
            "status_code": 0,
            "response_received": False,
            "write_reached_transport": False,
        },
        campaign_id=exp["campaign_id"],
        execution_id=exp["execution_id"],
    )
    auth_obs = build_observer_receipt(
        observer_id="authorization_comparison",
        status="INDETERMINATE",
        reason_code="AUTHORIZED_CONTROL_FAILED",
        evidence={"step_id": "treatment_1", "control_status": 0, "treatment_status": 0},
        campaign_id=exp["campaign_id"],
        execution_id=exp["execution_id"],
    )
    activation = build_contract_oracle_activation_receipt(
        experiment=exp,
        evidence={
            "contract_evidence_receipts": [control, treatment, actor, actor2],
            "observer_receipts": [http_obs, auth_obs],
        },
    )
    assert activation["status"] == "BLOCKED"
    assert activation["status"] != "HARNESS_FAILED"
    assert any(
        code.startswith("CONTROL_RECEIPT_BLOCKED:")
        for code in activation["reason_codes"]
    )
    assert not any(
        "RECEIPT_FAILED" in code for code in activation["reason_codes"]
    )


def test_zero_transport_step_scoped_http_response_is_indeterminate() -> None:
    receipts = _step_scoped_http_response_receipts(
        observations={
            "execution_steps": [
                {
                    "step_id": "control_1",
                    "phase": "control",
                    "operation_ref": "bir_example",
                    "status_code": 0,
                    "body": None,
                    "governance_receipt": {
                        "write_request_attempt_count": 0,
                        "before": {"status": 404},
                        "write": {"status": 0},
                        "reason": "governed_write_identity_unobservable",
                    },
                }
            ]
        },
        aggregate_receipt={
            "receipt_id": "obs_aggregate",
            "campaign_id": "cmp_zero_transport",
            "execution_id": "exec_zero_transport",
        },
    )
    assert len(receipts) == 1
    assert receipts[0]["status"] == "INDETERMINATE"
    assert receipts[0]["reason_code"] == "HTTP_RESPONSE_NOT_ATTEMPTED"
    assert receipts[0]["evidence"]["write_reached_transport"] is False


def test_attempted_cleanup_without_accept_is_not_required_operationally() -> None:
    """Adapter/cleanup probes must not seal COMPLETED with completed_count=0."""
    receipt = build_execution_operational_receipt(
        receipt_id="operational-cei-false",
        execution_status="EXECUTED",
        steps=[
            {
                "phase": "treatment",
                "method": "PATCH",
                "path": "/api/cart/items/1",
                "status_code": 200,
                "governance_receipt": {
                    "status": "executed",
                    "accepted": True,
                    "http_attempt_count": 2,
                    "write_request_attempt_count": 1,
                    "production_http_requests": 0,
                    "method": "PATCH",
                },
            },
            {
                "phase": "cleanup",
                "method": "ADAPTER_DB_SQL",
                "path": "",
                "status_code": 0,
                "governance_receipt": {
                    "status": "not_required",
                    "accepted": False,
                    "http_attempt_count": 0,
                    "write_request_attempt_count": 0,
                    "production_http_requests": 0,
                    "method": "ADAPTER_DB_SQL",
                },
            },
        ],
        cleanup_failures=0,
    )
    assert receipt["accepted_non_cleanup_write_count"] == 1
    assert receipt["cleanup_outcome"]["attempted_count"] == 1
    assert receipt["cleanup_outcome"]["completed_count"] == 0
    assert receipt["cleanup_outcome"]["status"] == "NOT_REQUIRED"
