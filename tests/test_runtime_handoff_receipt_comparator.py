from __future__ import annotations

from ai_test_asset_center.runtime_handoff_receipt_comparator import compare_immutable_run_receipts


def _receipt(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "run_lineage_id": "qbrun-demo",
        "probe_plan_hash": "plan",
        "runtime_evidence_sla_gate_hash": "sla",
        "runtime_sla_execution_policy_hash": "policy",
        "commercial_handoff_bundle_hash": "bundle",
        "commercial_handoff_acceptance_gate_hash": "acceptance",
        "commercial_handoff_secret_audit_hash": "secret",
        "remediation_verification_hash": "remediation",
        "artifact_archive_hash": "archive",
        "minimum_commercial_gate_failures": [],
        "commercial_blocking_reasons": [],
        "customer_acceptance_violation_count": 0,
        "customer_acceptance_violation_ids": [],
    }
    base.update(overrides)
    return {"immutable_run_receipt": base}


def test_receipt_comparison_surfaces_minimum_gate_and_acceptance_changes() -> None:
    previous = _receipt(
        minimum_commercial_gate_failures=["auth_session_verified"],
        commercial_blocking_reasons=["auth_session_ready"],
        customer_acceptance_violation_count=1,
        customer_acceptance_violation_ids=["HANDOFF-MINIMUM-COMMERCIAL-GATE-FAILED"],
    )
    current = _receipt()

    comparison = compare_immutable_run_receipts(current, previous)
    change_fields = {change["field"] for change in comparison["changes"]}

    assert comparison["status"] == "rerun_same_input_delivery_changed"
    assert "minimum_commercial_gate_failures" in change_fields
    assert "commercial_blocking_reasons" in change_fields
    assert "customer_acceptance_violation_count" in change_fields
    assert "customer_acceptance_violation_ids" in change_fields
    assert comparison["lineage_match"] is True


def test_receipt_comparison_normalizes_list_order() -> None:
    previous = _receipt(minimum_commercial_gate_failures=["base_url_configured", "auth_session_verified"])
    current = _receipt(minimum_commercial_gate_failures=["auth_session_verified", "base_url_configured"])

    comparison = compare_immutable_run_receipts(current, previous)

    assert "minimum_commercial_gate_failures" not in {change["field"] for change in comparison["changes"]}
