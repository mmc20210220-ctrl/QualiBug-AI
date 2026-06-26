from __future__ import annotations

from ai_test_asset_center.runtime_external_tracker_sync_payload_gate import validate_external_tracker_sync_payloads


def _blocked_source_policy() -> dict[str, object]:
    return {
        "status": "external_tracker_closure_sync_blocked",
        "policies": [
            {
                "sync_policy_id": "SYNC-0001",
                "sync_status": "sync_blocked_by_lineage_audit",
                "blocked": True,
                "audit_blocker_ids": ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"],
                "audit_blocker_details": [
                    {
                        "gate_id": "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED",
                        "reason": "Minimum commercial gate failures changed.",
                    }
                ],
            }
        ],
    }


def test_payload_gate_blocks_resolution_payload_from_blocked_policy_id() -> None:
    gate = validate_external_tracker_sync_payloads(
        {
            "external_tracker_closure_sync_policy": _blocked_source_policy(),
            "external_tracker_sync_payloads": {
                "dry_run_only": True,
                "source_policy_status": "external_tracker_closure_sync_blocked",
                "jira_transition_payload_count": 1,
                "linear_update_payload_count": 0,
                "csv_status_update_count": 0,
                "hold_item_count": 0,
                "jira_transition_payloads": [
                    {
                        "sync_policy_id": "SYNC-0001",
                        "issue_id_or_key": "JIRA-123",
                        "transition": {"target_status": "Resolved"},
                        "comment": "attempted unsafe closure",
                        "dry_run_only": True,
                    }
                ],
            },
        }
    )

    assert gate["status"] == "external_tracker_sync_payload_gate_blocked"
    assert gate["payload_import_ready"] is False
    violation = next(v for v in gate["violations"] if v["kind"] == "resolution_payload_from_blocked_policy")
    assert violation["sync_policy_id"] == "SYNC-0001"
    assert violation["audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]


def test_payload_gate_requires_hold_items_to_keep_audit_blocker_ids() -> None:
    gate = validate_external_tracker_sync_payloads(
        {
            "external_tracker_closure_sync_policy": _blocked_source_policy(),
            "external_tracker_sync_payloads": {
                "dry_run_only": True,
                "source_policy_status": "external_tracker_closure_sync_blocked",
                "jira_transition_payload_count": 0,
                "linear_update_payload_count": 0,
                "csv_status_update_count": 0,
                "hold_item_count": 1,
                "hold_items": [
                    {
                        "sync_policy_id": "SYNC-0001",
                        "ledger_entry_id": "CLAIM-0001",
                        "sync_status": "sync_blocked_by_lineage_audit",
                        "comment": "hold open",
                    }
                ],
            },
        }
    )

    assert gate["status"] == "external_tracker_sync_payload_gate_blocked"
    violation = next(v for v in gate["violations"] if v["kind"] == "hold_item_missing_audit_blockers")
    assert violation["expected_audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
