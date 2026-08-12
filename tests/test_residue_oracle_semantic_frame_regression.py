"""Regression coverage for residue cleanup proof and incomplete rule frames."""
from __future__ import annotations

from ai_test_asset_center.contract_oracles import (
    build_contract_evidence_receipt,
    build_contract_oracle_activation_receipt,
    validate_contract_oracle_activation_receipt,
)
from ai_test_asset_center.enterprise_knowledge_center._parsing import (
    _rules_from_text,
    _semantic_rule_frame,
)
from ai_test_asset_center.observer_contracts import build_observer_receipt


def _experiment() -> dict:
    return {
        "experiment_id": "exp_residue",
        "obligation_id": "obl_residue",
        "campaign_id": "CMP_residue",
        "execution_id": "EXEC_residue",
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
        "cleanup_plan": [
            {"step_id": "cleanup_residue", "kind": "accepted_residue"}
        ],
    }


def _contract(kind: str, subject_id: str, status: str = "OBSERVED", **evidence: object) -> dict:
    return build_contract_evidence_receipt(
        kind=kind,
        experiment_id="exp_residue",
        obligation_id="obl_residue",
        campaign_id="CMP_residue",
        execution_id="EXEC_residue",
        subject_id=subject_id,
        status=status,
        evidence=evidence,
    )


def _http_observer(step_id: str) -> dict:
    return build_observer_receipt(
        observer_id="http_response",
        status="OBSERVED",
        evidence={
            "step_id": step_id,
            "phase": "control" if step_id == "control_1" else "treatment",
            "status_code": 200 if step_id == "control_1" else 403,
            "response_received": True,
            "scope_basis": "execution_step_identity",
        },
        campaign_id="CMP_residue",
        execution_id="EXEC_residue",
    )


def _evidence(*, residue: bool, reason_code: str) -> dict:
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
            _contract(
                "cleanup",
                "cleanup_residue",
                status="RESIDUE_ACCEPTED",
                residue=residue,
                reason_code=reason_code,
                accepted_write_count=1,
                cleanup_write_count=0,
            ),
        ],
        "observer_receipts": [
            _http_observer("control_1"),
            _http_observer("treatment_1"),
            build_observer_receipt(
                observer_id="actor_identity",
                status="OBSERVED",
                evidence={"actor_ref_fingerprints": ["x"], "distinct_actor_count": 1},
                campaign_id="CMP_residue",
                execution_id="EXEC_residue",
            ),
        ],
    }


def test_residue_accepted_with_explicit_evidence_satisfies_cleanup_contract() -> None:
    activation = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence=_evidence(
            residue=True,
            reason_code="ACCEPTED_RESIDUE_NO_CLEANUP",
        ),
    )
    assert activation["status"] == "ACTIVE"
    assert "CLEANUP_RESTORATION_NOT_PROVEN:cleanup_residue" not in activation["reason_codes"]
    assert validate_contract_oracle_activation_receipt(activation)["status"] == "ACTIVE"


def test_residue_accepted_without_residue_flag_remains_fail_closed() -> None:
    activation = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence=_evidence(
            residue=False,
            reason_code="ACCEPTED_RESIDUE_NO_CLEANUP",
        ),
    )
    assert activation["status"] == "BLOCKED"
    assert "CLEANUP_RESTORATION_NOT_PROVEN:cleanup_residue" in activation["reason_codes"]


def test_residue_accepted_with_wrong_reason_remains_fail_closed() -> None:
    activation = build_contract_oracle_activation_receipt(
        experiment=_experiment(),
        evidence=_evidence(residue=True, reason_code="UNDECLARED_RESIDUE"),
    )
    assert activation["status"] == "BLOCKED"
    assert "CLEANUP_RESTORATION_NOT_PROVEN:cleanup_residue" in activation["reason_codes"]


def test_semantic_rule_frame_rejects_marker_only_tail_without_behavior() -> None:
    assert _semantic_rule_frame("403 角色或数据范围不允许") == {}
    assert _semantic_rule_frame("当前资源仅限") == {}
    assert _semantic_rule_frame("required:") == {}
    assert _rules_from_text(
        "403 角色或数据范围不允许。\n当前资源仅限。\nrequired:",
        "src_api_contract",
        "business_rules",
    ) == []
