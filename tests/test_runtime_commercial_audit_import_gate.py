from __future__ import annotations

from ai_test_asset_center.runtime_commercial_audit_export_import_gate import build_commercial_audit_export_import_gate


def test_import_gate_blocks_blocked_closure_exports_without_audit_blocker_ids() -> None:
    gate = build_commercial_audit_export_import_gate(
        {
            "project_id": "demo",
            "commercial_audit_export_adapters": {
                "event_count": 1,
                "run_lineage_id": "qbrun-demo",
                "jira_issue_import": [
                    {
                        "external_tracking_key": "QB-AUDIT-DEMO-AUDIT-CLOSURE-0001",
                        "projectKey": "<FILL:jira_project_key>",
                        "issueType": "Task",
                        "summary": "Blocked closure claim",
                        "description": "blocked closure",
                        "priority": "Highest",
                    }
                ],
                "linear_issue_import": [
                    {
                        "external_tracking_key": "QB-AUDIT-DEMO-AUDIT-CLOSURE-0001-L",
                        "teamId": "<FILL:linear_team_id>",
                        "title": "Blocked closure claim",
                        "description": "blocked closure",
                        "priority": 1,
                    }
                ],
                "csv_audit_ledger_rows": [
                    {
                        "external_tracking_key": "QB-AUDIT-DEMO-AUDIT-CLOSURE-0001-C",
                        "event_id": "AUDIT-CLOSURE-0001",
                        "event_kind": "finding_closure_claim_recorded",
                        "severity": "critical",
                        "summary": "blocked",
                        "run_lineage_id": "qbrun-demo",
                        "commercial_acceptance_status": "blocked_by_lineage_audit",
                    }
                ],
                "closure_external_tracking_keys": [
                    {
                        "external_tracking_key": "QB-CLOSURE-DEMO-CLAIM-0001",
                        "ledger_entry_id": "CLAIM-0001",
                        "commercial_acceptance_status": "blocked_by_lineage_audit",
                        "blocked": True,
                    }
                ],
            },
        }
    )

    kinds = {violation["kind"] for violation in gate["violations"]}

    assert gate["status"] == "commercial_audit_import_gate_blocked"
    assert "csv_blocked_closure_missing_audit_blocker_ids" in kinds
    assert "closure_tracking_key_missing_audit_blocker_ids" in kinds


def test_import_gate_allows_blocked_closure_exports_with_audit_blocker_ids() -> None:
    gate = build_commercial_audit_export_import_gate(
        {
            "project_id": "demo",
            "commercial_audit_export_adapters": {
                "event_count": 1,
                "run_lineage_id": "qbrun-demo",
                "jira_issue_import": [],
                "linear_issue_import": [],
                "csv_audit_ledger_rows": [
                    {
                        "external_tracking_key": "QB-AUDIT-DEMO-AUDIT-CLOSURE-0001-C",
                        "event_id": "AUDIT-CLOSURE-0001",
                        "event_kind": "finding_closure_claim_recorded",
                        "severity": "critical",
                        "summary": "blocked",
                        "run_lineage_id": "qbrun-demo",
                        "commercial_acceptance_status": "blocked_by_lineage_audit",
                        "audit_blocker_ids": "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED",
                    }
                ],
                "closure_external_tracking_keys": [
                    {
                        "external_tracking_key": "QB-CLOSURE-DEMO-CLAIM-0001",
                        "ledger_entry_id": "CLAIM-0001",
                        "commercial_acceptance_status": "blocked_by_lineage_audit",
                        "blocked": True,
                        "audit_blocker_ids": ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"],
                    }
                ],
            },
        }
    )

    kinds = {violation["kind"] for violation in gate["violations"]}

    assert "csv_blocked_closure_missing_audit_blocker_ids" not in kinds
    assert "closure_tracking_key_missing_audit_blocker_ids" not in kinds
