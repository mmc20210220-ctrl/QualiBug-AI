from __future__ import annotations

from ai_test_asset_center.runtime_external_tracker_sync_receipt_ledger import build_external_tracker_sync_receipt_ledger


def test_sync_receipt_ledger_records_payload_gate_violations_and_hold_blockers() -> None:
    ledger = build_external_tracker_sync_receipt_ledger(
        {
            "project_id": "demo",
            "external_tracker_sync_payload_gate": {
                "status": "external_tracker_sync_payload_gate_blocked",
                "payload_import_ready": False,
                "violation_count": 1,
                "violations": [
                    {
                        "kind": "resolution_payload_from_blocked_policy",
                        "sync_policy_id": "SYNC-0001",
                        "audit_blocker_ids": ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"],
                    }
                ],
            },
            "external_tracker_sync_payloads": {
                "run_lineage_id": "qbrun-demo",
                "hold_items": [
                    {
                        "sync_policy_id": "SYNC-0001",
                        "external_closure_tracking_key": "EXT-CLOSE-1",
                        "hold_reason": "Closure ledger blocks this claim by lineage audit.",
                        "audit_blocker_ids": ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"],
                        "audit_blocker_details": [
                            {
                                "gate_id": "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED",
                                "severity": "P0",
                                "reason": "Minimum commercial gate failures changed.",
                            }
                        ],
                    }
                ],
            },
        }
    )

    entry = ledger["entries"][0]

    assert ledger["status"] == "external_tracker_sync_receipt_blocked_by_payload_gate"
    assert ledger["payload_gate_violation_count"] == 1
    assert ledger["payload_gate_violation_kinds"] == ["resolution_payload_from_blocked_policy"]
    assert entry["receipt_status"] == "hold_not_synced"
    assert entry["audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
    assert entry["audit_blocker_details"][0]["severity"] == "P0"
