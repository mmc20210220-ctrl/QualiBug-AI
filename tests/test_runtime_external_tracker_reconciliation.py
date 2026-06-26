from __future__ import annotations

from ai_test_asset_center.runtime_commercial_external_tracker_reconciliation import build_commercial_external_tracker_reconciliation
from ai_test_asset_center.runtime_external_tracker_closure_sync_policy import build_external_tracker_closure_sync_policy


def _blocked_import_report() -> dict[str, object]:
    return {
        "project_id": "demo",
        "commercial_audit_export_adapters": {
            "run_lineage_id": "qbrun-demo",
            "jira_issue_import": [
                {"external_tracking_key": "QB-AUDIT-DEMO-1", "summary": "blocked audit event"}
            ],
            "closure_external_tracking_keys": [
                {"external_tracking_key": "QB-CLOSURE-DEMO-CLAIM-0001", "ledger_entry_id": "CLAIM-0001"}
            ],
        },
        "commercial_audit_export_import_gate": {
            "status": "commercial_audit_import_gate_blocked",
            "import_ready": False,
            "violation_count": 1,
            "violations": [
                {
                    "kind": "closure_tracking_key_missing_audit_blocker_ids",
                    "ledger_entry_id": "CLAIM-0001",
                }
            ],
        },
        "commercial_closure_acceptance_ledger": {
            "project_id": "demo",
            "ledger_entries": [
                {
                    "ledger_entry_id": "CLAIM-0001",
                    "commercial_acceptance_status": "accepted_for_customer_closure",
                    "endpoint": "POST /api/v1/orders",
                }
            ],
        },
        "commercial_handoff_secret_audit": {"safe_for_customer_handoff": True},
        "handoff_rerun_audit_gate": {"closure_verification_allowed": True},
        "immutable_run_receipt": {"run_lineage_id": "qbrun-demo"},
    }


def test_reconciliation_records_import_gate_violation_details() -> None:
    reconciliation = build_commercial_external_tracker_reconciliation(_blocked_import_report())
    entry = reconciliation["entries"][0]

    assert reconciliation["status"] == "external_tracker_reconciliation_blocked_by_import_gate"
    assert reconciliation["import_gate_violation_kinds"] == ["closure_tracking_key_missing_audit_blocker_ids"]
    assert entry["reconciliation_status"] == "blocked_by_import_gate"
    assert entry["import_gate_violation_kinds"] == ["closure_tracking_key_missing_audit_blocker_ids"]


def test_closure_sync_policy_receives_reconciliation_import_gate_violations() -> None:
    report = _blocked_import_report()
    report["commercial_external_tracker_reconciliation"] = build_commercial_external_tracker_reconciliation(report)

    policy = build_external_tracker_closure_sync_policy(report)
    item = policy["policies"][0]

    assert policy["status"] == "external_tracker_closure_sync_blocked"
    assert item["sync_status"] == "sync_blocked_by_import_gate"
    assert item["import_gate_violation_kinds"] == ["closure_tracking_key_missing_audit_blocker_ids"]
    assert item["import_gate_violations"][0]["ledger_entry_id"] == "CLAIM-0001"
