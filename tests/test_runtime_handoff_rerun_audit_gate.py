from __future__ import annotations

from ai_test_asset_center.runtime_handoff_rerun_audit_gate import build_handoff_rerun_audit_gate


def test_rerun_audit_blocks_when_minimum_commercial_gate_changes() -> None:
    gate = build_handoff_rerun_audit_gate(
        {
            "handoff_receipt_comparison": {
                "previous_receipt_present": True,
                "status": "rerun_same_input_delivery_changed",
                "current_run_lineage_id": "qbrun-demo",
                "previous_run_lineage_id": "qbrun-demo",
                "lineage_match": True,
                "input_hash_match": True,
                "artifact_archive_hash_match": True,
                "change_count": 1,
                "changes": [
                    {
                        "field": "minimum_commercial_gate_failures",
                        "category": "commercial_gate",
                        "reason": "Minimum commercial gate failure set changed.",
                    }
                ],
            }
        }
    )

    assert gate["status"] == "rerun_closure_audit_blocked"
    assert gate["closure_verification_allowed"] is False
    assert gate["commercial_lineage_claim"] == "new_or_invalid_lineage"
    assert any(item["gate_id"] == "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED" for item in gate["blockers"])


def test_rerun_audit_blocks_when_customer_acceptance_state_changes() -> None:
    gate = build_handoff_rerun_audit_gate(
        {
            "handoff_receipt_comparison": {
                "previous_receipt_present": True,
                "status": "rerun_same_input_delivery_changed",
                "lineage_match": True,
                "input_hash_match": True,
                "change_count": 1,
                "changes": [
                    {
                        "field": "customer_acceptance_violation_ids",
                        "category": "acceptance_gate",
                        "reason": "Customer acceptance violation set changed.",
                    }
                ],
            }
        }
    )

    assert gate["status"] == "rerun_closure_audit_blocked"
    assert gate["closure_verification_allowed"] is False
    assert any(item["gate_id"] == "RERUN-CUSTOMER-ACCEPTANCE-GATE-CHANGED" for item in gate["blockers"])


def test_rerun_audit_still_allows_reviewer_flow_for_non_gate_delivery_changes() -> None:
    gate = build_handoff_rerun_audit_gate(
        {
            "handoff_receipt_comparison": {
                "previous_receipt_present": True,
                "status": "rerun_same_input_delivery_changed",
                "lineage_match": True,
                "input_hash_match": True,
                "change_count": 1,
                "changes": [
                    {
                        "field": "artifact_archive_hash",
                        "category": "artifact_archive",
                        "reason": "Generated artifact archive changed.",
                    }
                ],
            }
        }
    )

    assert gate["status"] == "rerun_closure_conditional_reviewer_required"
    assert gate["closure_verification_allowed"] is True
    assert gate["blockers"] == []
