from __future__ import annotations

from ai_test_asset_center.enterprise_test_data_plan import validate_test_data_contract
from ai_test_asset_center.enterprise_test_data_receipts import (
    issue_test_data_receipt,
    verify_test_data_receipt,
)


def _verifier(root):
    def verify(kind: str, receipt_id: str, campaign_id: str, scope_id: str, environment_ref: str) -> bool:
        return verify_test_data_receipt(
            "enterprise-project",
            receipt_id,
            root=root,
            kind=kind,
            campaign_id=campaign_id,
            scope_id=scope_id,
            environment_ref=environment_ref,
        )["valid"]
    return verify


def test_disposable_contract_requires_real_creation_and_cleanup_receipts(tmp_path):
    campaign_id = "CMP_1"
    creation = issue_test_data_receipt(
        "enterprise-project",
        root=tmp_path,
        kind="creation",
        campaign_id=campaign_id,
        scope_id="scope-a",
        environment_ref="test-a",
        actor={"name": "qa", "role": "qa_lead"},
        data_scope_ref="sandbox-run-1",
    )
    cleanup = issue_test_data_receipt(
        "enterprise-project",
        root=tmp_path,
        kind="cleanup",
        campaign_id=campaign_id,
        scope_id="scope-a",
        environment_ref="test-a",
        actor={"name": "qa", "role": "qa_lead"},
        operation_ref="cleanup-run-1",
    )
    contract = {
        "strategy": "create_disposable",
        "write_approved": True,
        "campaign_id": campaign_id,
        "scope_id": "scope-a",
        "environment_ref": "test-a",
        "disposable_scope_ref": "sandbox-run-1",
        "creation_receipt_ref": creation["receipt_id"],
        "cleanup_receipt_ref": cleanup["receipt_id"],
    }

    ready = validate_test_data_contract(contract, environment_ref="", scope_id="", campaign_id=campaign_id, receipt_verifier=_verifier(tmp_path))
    forged = validate_test_data_contract({**contract, "cleanup_receipt_ref": "tdr_forged"}, environment_ref="", scope_id="", campaign_id=campaign_id, receipt_verifier=_verifier(tmp_path))

    assert ready["status"] == "ready"
    assert ready["receipt_validation"] == "verified"
    assert forged["status"] == "blocked_with_testability_gap"
    assert "DATA_CLEANUP_RECEIPT_INVALID" in forged["missing_requirements"]


def test_receipt_scope_or_environment_mismatch_is_rejected(tmp_path):
    receipt = issue_test_data_receipt(
        "enterprise-project",
        root=tmp_path,
        kind="provenance",
        campaign_id="CMP_1",
        scope_id="scope-a",
        environment_ref="test-a",
        actor={"name": "qa", "role": "qa_lead"},
        provenance_ref="fixture-ledger",
    )
    verification = verify_test_data_receipt(
        "enterprise-project",
        receipt["receipt_id"],
        root=tmp_path,
        kind="provenance",
        campaign_id="CMP_1",
        scope_id="scope-a",
        environment_ref="other-env",
    )

    assert verification["valid"] is False
    assert verification["code"] == "TEST_DATA_RECEIPT_ENVIRONMENT_REF_MISMATCH"
