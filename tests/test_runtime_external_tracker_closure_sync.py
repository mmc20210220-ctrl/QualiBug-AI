from __future__ import annotations

from ai_test_asset_center.runtime_external_tracker_closure_sync_policy import build_external_tracker_closure_sync_policy
from ai_test_asset_center.runtime_external_tracker_sync_payload_builder import build_external_tracker_sync_payloads


def _blocked_report() -> dict[str, object]:
    return {
        "project_id": "demo",
        "commercial_closure_acceptance_ledger": {
            "project_id": "demo",
            "ledger_entries": [
                {
                    "ledger_entry_id": "CLAIM-0001",
                    "previous_finding_id": "F-1",
                    "candidate_id": "C-1",
                    "endpoint": "POST /api/v1/orders",
                    "commercial_acceptance_status": "blocked_by_lineage_audit",
                    "audit_blocker_ids": ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"],
                    "audit_blocker_details": [
                        {
                            "gate_id": "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED",
                            "severity": "P0",
                            "reason": "Minimum commercial gate failures changed.",
                            "changed_fields": ["minimum_commercial_gate_failures"],
                        }
                    ],
                }
            ],
        },
        "commercial_audit_export_adapters": {
            "closure_external_tracking_keys": [
                {"ledger_entry_id": "CLAIM-0001", "external_tracking_key": "EXT-CLOSE-1"}
            ]
        },
        "commercial_external_tracker_reconciliation": {
            "status": "external_tracker_reconciliation_ready",
            "entries": [
                {
                    "source_kind": "closure_ledger",
                    "external_tracking_key": "EXT-CLOSE-1",
                    "reconciliation_status": "reconciled_import_confirmed",
                    "system": "jira",
                    "external_id": "JIRA-123",
                }
            ],
        },
        "commercial_audit_export_import_gate": {"status": "ready", "import_ready": True},
        "commercial_handoff_secret_audit": {"status": "safe", "safe_for_customer_handoff": True},
        "handoff_rerun_audit_gate": {"status": "rerun_closure_audit_blocked", "closure_verification_allowed": False},
        "immutable_run_receipt": {"run_lineage_id": "qbrun-demo", "receipt_hash": "receipt"},
        "outputs": {},
    }


def test_external_tracker_policy_keeps_audit_blocker_details_for_blocked_closure() -> None:
    policy = build_external_tracker_closure_sync_policy(_blocked_report())
    item = policy["policies"][0]

    assert policy["status"] == "external_tracker_closure_sync_blocked"
    assert item["sync_status"] == "sync_blocked_by_lineage_audit"
    assert item["blocked"] is True
    assert item["audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
    assert item["audit_blocker_details"][0]["changed_fields"] == ["minimum_commercial_gate_failures"]


def test_external_tracker_payloads_hold_blocked_closure_with_blocker_comment() -> None:
    report = _blocked_report()
    report["external_tracker_closure_sync_policy"] = build_external_tracker_closure_sync_policy(report)

    payloads = build_external_tracker_sync_payloads(report)
    hold = payloads["hold_items"][0]

    assert payloads["status"] == "external_tracker_sync_payloads_blocked_or_empty"
    assert payloads["jira_transition_payloads"] == []
    assert hold["audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
    assert "audit_blockers:" in hold["comment"]
    assert "Minimum commercial gate failures changed." in hold["comment"]
