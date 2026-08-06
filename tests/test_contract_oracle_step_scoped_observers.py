"""Regression: exact process-step scoped observers are not duplicates.

The exact process-step enrichment replaces one aggregate http_response observer
receipt with one receipt per explicit plan step (control_1, treatment_1), each
carrying ``evidence.step_id``. The contract oracle activation validator treated
the second http_response receipt as a hard duplicate and failed the whole
experiment with ``OBSERVER_RECEIPT_DUPLICATE:http_response``, which dominated
the benchmark funnel (88/89 harness failures). Distinct step scopes are valid
evidence for distinct plan steps; only the same observer_id + step scope is a
true duplicate.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center._contract_oracles_mechanics import (
    build_contract_oracle_activation_receipt,
    validate_contract_oracle_activation_receipt,
)
from ai_test_asset_center.contract_oracles import build_contract_evidence_receipt
from ai_test_asset_center.observer_contracts import build_observer_receipt


def _experiment() -> dict:
    return {
        "experiment_id": "exp_scope",
        "obligation_id": "obl_scope",
        "campaign_id": "CMP_scope",
        "execution_id": "EXEC_scope",
        "risk_family": "authorization",
        "source_refs": [{"source_id": "src_1", "locator": "POST /api/orders"}],
        "control_plan": [
            {
                "step_id": "control_1",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_order",
            }
        ],
        "treatment_plan": [
            {
                "step_id": "treatment_1",
                "actor_ref": "actor_buyer",
                "operation_ref": "op_order",
            }
        ],
        "observers": [
            {"observer_id": "http_response", "surface": "http_api"},
            {"observer_id": "actor_identity", "surface": "http_api"},
        ],
        "assertions": [{"kind": "authorization", "property": {}}],
        "fixture_dag": {"status": "READY", "nodes": [], "setup_order": []},
        "cleanup_plan": [],
    }


def _contract(kind: str, subject_id: str, **evidence: object) -> dict:
    return build_contract_evidence_receipt(
        kind=kind,
        experiment_id="exp_scope",
        obligation_id="obl_scope",
        campaign_id="CMP_scope",
        execution_id="EXEC_scope",
        subject_id=subject_id,
        status="OBSERVED",
        evidence=evidence,
    )


def _evidence(observer_receipts: list[dict]) -> dict:
    return {
        "contract_evidence_receipts": [
            _contract(
                "control",
                "control_1",
                response_observed=True,
                control_succeeded=True,
                status_code=200,
            ),
            _contract(
                "treatment",
                "treatment_1",
                response_observed=True,
                status_code=403,
            ),
            _contract(
                "actor",
                "actor_buyer",
                role="buyer",
                credential_secret_ref_present=True,
                credential_material_observed=True,
            ),
        ],
        "observer_receipts": observer_receipts,
    }


def _step_scoped_http_response(step_id: str) -> dict:
    return build_observer_receipt(
        observer_id="http_response",
        status="OBSERVED",
        evidence={
            "step_id": step_id,
            "phase": "control" if step_id.startswith("control") else "treatment",
            "status_code": 200 if step_id.startswith("control") else 403,
            "response_received": True,
            "scope_basis": "execution_step_identity",
        },
        campaign_id="CMP_scope",
        execution_id="EXEC_scope",
    )


def test_distinct_step_scopes_are_not_duplicates() -> None:
    activation = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence=_evidence(
            [
                _step_scoped_http_response("control_1"),
                _step_scoped_http_response("treatment_1"),
                build_observer_receipt(
                    observer_id="actor_identity",
                    status="OBSERVED",
                    evidence={"actor_ref_fingerprints": ["x"], "distinct_actor_count": 1},
                    campaign_id="CMP_scope",
                    execution_id="EXEC_scope",
                ),
            ]
        ),
    )
    assert activation["status"] == "ACTIVE"
    assert "OBSERVER_RECEIPT_DUPLICATE:http_response" not in activation.get(
        "reason_codes", []
    )
    # The sealed activation receipt must round-trip through the validator.
    validated = validate_contract_oracle_activation_receipt(activation)
    assert validated["status"] == "ACTIVE"
    assert len(validated["verified_receipt_ids"]["observer"]) == len(
        validated["required"]["observer"]
    )


def test_true_duplicate_same_step_scope_still_fails() -> None:
    activation = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence=_evidence(
            [
                _step_scoped_http_response("control_1"),
                _step_scoped_http_response("control_1"),
                build_observer_receipt(
                    observer_id="actor_identity",
                    status="OBSERVED",
                    evidence={"actor_ref_fingerprints": ["x"], "distinct_actor_count": 1},
                    campaign_id="CMP_scope",
                    execution_id="EXEC_scope",
                ),
            ]
        ),
    )
    assert activation["status"] == "HARNESS_FAILED"
    assert "OBSERVER_RECEIPT_DUPLICATE:http_response" in activation.get(
        "reason_codes", []
    )


def test_accepted_residue_cleanup_receipt_activates_experiment() -> None:
    """A RESIDUE_ACCEPTED cleanup receipt is the environment-gated degradation
    outcome, not a cleanup failure: the compiler emitted accepted_residue only
    on a declared non-production target, the delivery gate short-circuits on
    this status, and the oracle must not re-block with
    CLEANUP_RESTORATION_NOT_PROVEN."""
    experiment = _experiment()
    experiment["cleanup_plan"] = [
        {
            "step_id": "cleanup_1",
            "action": "accepted_residue",
            "mode": "accepted_residue_no_cleanup",
            "residue_notice": "no_source_compensator:op_order",
        }
    ]
    evidence = _evidence(
        [
            _step_scoped_http_response("control_1"),
            _step_scoped_http_response("treatment_1"),
            build_observer_receipt(
                observer_id="actor_identity",
                status="OBSERVED",
                evidence={"actor_ref_fingerprints": ["x"], "distinct_actor_count": 1},
                campaign_id="CMP_scope",
                execution_id="EXEC_scope",
            ),
        ]
    )
    evidence["contract_evidence_receipts"].append(
        build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id="exp_scope",
            obligation_id="obl_scope",
            campaign_id="CMP_scope",
            execution_id="EXEC_scope",
            subject_id="cleanup_1",
            status="RESIDUE_ACCEPTED",
            evidence={
                "accepted_write_count": 1,
                "cleanup_required_write_count": 1,
                "cleanup_write_count": 0,
                "state_unchanged": False,
                "restoration_verified": False,
                "audit_receipt_ids": [],
                "reason_code": "ACCEPTED_RESIDUE_NO_CLEANUP",
                "cleanup_mode": "accepted_residue_no_cleanup",
                "residue": True,
                "residue_notice": "no_source_compensator:op_order",
                "compensates_operation_ref": "op_order",
            },
        )
    )
    activation = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )
    assert "CLEANUP_RESTORATION_NOT_PROVEN:cleanup_1" not in (
        activation.get("reason_codes") or []
    )
    # The residue receipt itself is accepted as the cleanup proof.
    assert len(activation["verified_receipt_ids"]["cleanup"]) == 1
    assert (
        activation["verified_receipt_ids"]["cleanup"][0]
        == evidence["contract_evidence_receipts"][-1]["receipt_id"]
    )


def test_residue_receipt_without_residue_flag_still_blocks_cleanup() -> None:
    """RESIDUE_ACCEPTED without the residue evidence flag is not the accepted
    degradation: it must keep blocking cleanup proof (mirrors the delivery
    gate's fail-closed reading of the same status)."""
    experiment = _experiment()
    experiment["cleanup_plan"] = [
        {
            "step_id": "cleanup_1",
            "action": "accepted_residue",
            "mode": "accepted_residue_no_cleanup",
        }
    ]
    evidence = _evidence(
        [
            _step_scoped_http_response("control_1"),
            _step_scoped_http_response("treatment_1"),
            build_observer_receipt(
                observer_id="actor_identity",
                status="OBSERVED",
                evidence={"actor_ref_fingerprints": ["x"], "distinct_actor_count": 1},
                campaign_id="CMP_scope",
                execution_id="EXEC_scope",
            ),
        ]
    )
    evidence["contract_evidence_receipts"].append(
        build_contract_evidence_receipt(
            kind="cleanup",
            experiment_id="exp_scope",
            obligation_id="obl_scope",
            campaign_id="CMP_scope",
            execution_id="EXEC_scope",
            subject_id="cleanup_1",
            status="RESIDUE_ACCEPTED",
            evidence={
                "accepted_write_count": 1,
                "cleanup_write_count": 0,
                "reason_code": "ACCEPTED_RESIDUE_NO_CLEANUP",
                # no "residue": True — status alone must not unlock the gate
            },
        )
    )
    activation = build_contract_oracle_activation_receipt(
        experiment=experiment,
        evidence=evidence,
    )
    assert "CLEANUP_RESTORATION_NOT_PROVEN:cleanup_1" in (
        activation.get("reason_codes") or []
    )
