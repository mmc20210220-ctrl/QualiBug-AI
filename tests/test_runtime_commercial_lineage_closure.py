from __future__ import annotations

from ai_test_asset_center.runtime_commercial_closure_acceptance_ledger import build_commercial_closure_acceptance_ledger
from ai_test_asset_center.runtime_commercial_lineage_reviewer_signoff import build_commercial_lineage_reviewer_signoff_packet


def _blocked_report() -> dict[str, object]:
    return {
        "project_id": "demo",
        "commercial_evidence_lineage_dashboard": {
            "closure_claim_state": "closure_claim_blocked",
            "current_run_lineage_id": "qbrun-current",
            "previous_run_lineage_id": "qbrun-previous",
            "finding_closure_claims": [
                {
                    "previous_finding_id": "F-1",
                    "candidate_id": "C-1",
                    "endpoint": "POST /api/v1/orders",
                    "claim_status": "claimed_closed",
                }
            ],
        },
        "handoff_rerun_audit_gate": {
            "status": "rerun_closure_audit_blocked",
            "blocker_count": 1,
            "blockers": [
                {
                    "gate_id": "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED",
                    "severity": "P0",
                    "reason": "Minimum commercial gate failures changed.",
                    "changed_fields": ["minimum_commercial_gate_failures"],
                }
            ],
        },
    }


def test_lineage_signoff_packet_preserves_rerun_audit_blocker_details() -> None:
    packet = build_commercial_lineage_reviewer_signoff_packet(_blocked_report())

    assert packet["status"] == "lineage_signoff_blocked_by_audit_gate"
    assert packet["signoff_blocked"] is True
    assert packet["blocked_gate_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
    assert packet["blocked_gate_details"][0]["changed_fields"] == ["minimum_commercial_gate_failures"]


def test_closure_acceptance_ledger_records_blocker_details_on_claims() -> None:
    report = _blocked_report()
    report["commercial_lineage_reviewer_signoff_packet"] = build_commercial_lineage_reviewer_signoff_packet(report)

    ledger = build_commercial_closure_acceptance_ledger(report)
    entry = ledger["ledger_entries"][0]

    assert ledger["status"] == "closure_acceptance_ledger_blocked"
    assert ledger["audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
    assert ledger["audit_blocker_details"][0]["severity"] == "P0"
    assert entry["commercial_acceptance_status"] == "blocked_by_lineage_audit"
    assert entry["blocked"] is True
    assert entry["audit_blocker_ids"] == ["RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED"]
    assert entry["audit_blocker_details"][0]["changed_fields"] == ["minimum_commercial_gate_failures"]
