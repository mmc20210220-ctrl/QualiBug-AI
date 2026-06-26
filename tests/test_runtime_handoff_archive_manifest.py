from __future__ import annotations

from ai_test_asset_center.runtime_handoff_archive_manifest import build_handoff_archive_manifest


def test_immutable_receipt_records_minimum_gate_failures_and_acceptance_violations() -> None:
    manifest = build_handoff_archive_manifest(
        {
            "project_id": "demo",
            "created_at": "2026-06-27T00:00:00Z",
            "engine": "grounded_probe_executor",
            "runtime_evidence_readiness_sla_gate": {
                "sla_gate_passed": False,
                "minimum_commercial_gate_failures": ["auth_session_verified"],
                "commercial_blocking_reasons": ["auth_session_ready"],
            },
            "runtime_sla_execution_policy": {},
            "commercial_handoff_bundle": {"status": "conditional_handoff_onboarding_delta_required"},
            "commercial_handoff_acceptance_gate": {
                "status": "acceptance_blocked",
                "acceptance_gate_passed": False,
                "violation_count": 1,
                "violations": [
                    {
                        "violation_id": "HANDOFF-MINIMUM-COMMERCIAL-GATE-FAILED",
                        "severity": "P0",
                    }
                ],
            },
            "commercial_handoff_secret_audit": {
                "status": "safe",
                "safe_for_customer_handoff": True,
            },
            "remediation_verification_artifact": {},
            "summary": {"runtime_evidence_readiness_score": 49},
            "outputs": {},
        }
    )

    receipt = manifest["immutable_run_receipt"]

    assert receipt["minimum_commercial_gate_failures"] == ["auth_session_verified"]
    assert receipt["commercial_blocking_reasons"] == ["auth_session_ready"]
    assert receipt["customer_acceptance_gate_passed"] is False
    assert receipt["customer_acceptance_violation_count"] == 1
    assert receipt["customer_acceptance_violation_ids"] == ["HANDOFF-MINIMUM-COMMERCIAL-GATE-FAILED"]
    assert receipt["customer_acceptance_status"] == "acceptance_blocked"
