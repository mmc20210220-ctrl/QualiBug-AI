from __future__ import annotations

from ai_test_asset_center.runtime_commercial_audit_event_stream import build_commercial_audit_event_stream
from ai_test_asset_center.runtime_commercial_audit_export_adapters import build_commercial_audit_export_adapters, render_csv_audit_ledger


def _blocked_report() -> dict[str, object]:
    return {
        "project_id": "demo",
        "immutable_run_receipt": {"run_lineage_id": "qbrun-demo", "receipt_status": "ready"},
        "handoff_receipt_comparison": {"previous_receipt_present": True, "status": "rerun_same_input_delivery_changed"},
        "handoff_rerun_audit_gate": {
            "status": "rerun_closure_audit_blocked",
            "blocker_count": 1,
            "closure_verification_allowed": False,
        },
        "commercial_evidence_lineage_dashboard": {"status": "lineage_dashboard_closure_blocked"},
        "commercial_lineage_reviewer_signoff_packet": {"status": "lineage_signoff_blocked_by_audit_gate", "signoff_blocked": True},
        "commercial_closure_acceptance_ledger": {
            "ledger_entries": [
                {
                    "ledger_entry_id": "CLAIM-0001",
                    "previous_finding_id": "F-1",
                    "endpoint": "POST /api/v1/orders",
                    "commercial_acceptance_status": "blocked_by_lineage_audit",
                    "blocked": True,
                    "requires_reviewer_signoff": False,
                    "audit_blocker_ids": ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"],
                    "audit_blocker_details": [
                        {
                            "gate_id": "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED",
                            "reason": "Minimum commercial gate failures changed.",
                        }
                    ],
                }
            ]
        },
    }


def test_audit_event_stream_carries_closure_blocker_details() -> None:
    stream = build_commercial_audit_event_stream(_blocked_report())
    closure_event = next(event for event in stream["events"] if event["event_id"] == "AUDIT-CLOSURE-0001")

    assert stream["status"] == "commercial_audit_event_stream_contains_blockers"
    assert closure_event["severity"] == "critical"
    assert closure_event["audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
    assert closure_event["audit_blocker_details"][0]["reason"] == "Minimum commercial gate failures changed."


def test_audit_exports_keep_blocker_ids_in_issue_description_csv_and_closure_keys() -> None:
    report = _blocked_report()
    report["commercial_audit_event_stream"] = build_commercial_audit_event_stream(report)

    exports = build_commercial_audit_export_adapters(report)
    closure_issue = next(issue for issue in exports["jira_issue_import"] if "AUDIT-CLOSURE-0001" in issue["summary"])
    closure_row = next(row for row in exports["csv_audit_ledger_rows"] if row["event_id"] == "AUDIT-CLOSURE-0001")
    closure_key = exports["closure_external_tracking_keys"][0]
    csv_text = render_csv_audit_ledger(exports)

    assert "audit_blocker_ids: RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED" in closure_issue["description"]
    assert closure_row["audit_blocker_ids"] == "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"
    assert closure_key["audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
    assert "audit_blocker_ids" in csv_text.splitlines()[0]
