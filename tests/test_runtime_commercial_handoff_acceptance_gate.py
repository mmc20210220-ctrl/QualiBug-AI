from __future__ import annotations

from ai_test_asset_center.runtime_commercial_handoff_acceptance_gate import validate_commercial_handoff_acceptance


def _ready_bundle() -> dict[str, object]:
    return {
        "status": "commercial_handoff_ready_no_validated_findings",
        "project_id": "demo",
        "executive_summary": {
            "commercial_readiness_score": 92,
            "sla_gate_passed": True,
            "minimum_commercial_gate_failures": [],
            "commercial_blocking_reasons": [],
        },
        "artifact_manifest": [
            {"artifact_key": "execution_report", "path": "grounded_probe_execution_report.json", "required_for_handoff": True}
        ],
        "customer_signoff_checklist": [
            {"item_id": "HANDOFF-SLA-GATE", "required": True, "passed": True},
            {"item_id": "HANDOFF-MINIMUM-COMMERCIAL-GATE", "required": True, "passed": True},
        ],
    }


def test_acceptance_gate_blocks_ready_bundle_with_minimum_gate_failures() -> None:
    bundle = _ready_bundle()
    bundle["executive_summary"] = {
        "commercial_readiness_score": 49,
        "sla_gate_passed": True,
        "minimum_commercial_gate_failures": ["auth_session_verified"],
        "commercial_blocking_reasons": ["auth_session_ready"],
    }

    gate = validate_commercial_handoff_acceptance(bundle)

    assert gate["acceptance_gate_passed"] is False
    assert gate["status"] == "acceptance_blocked"
    assert gate["minimum_commercial_gate_failures"] == ["auth_session_verified"]
    assert gate["commercial_blocking_reasons"] == ["auth_session_ready"]
    assert any(v["violation_id"] == "HANDOFF-MINIMUM-COMMERCIAL-GATE-FAILED" for v in gate["violations"])


def test_acceptance_gate_allows_ready_bundle_without_minimum_failures() -> None:
    gate = validate_commercial_handoff_acceptance(_ready_bundle())

    assert gate["acceptance_gate_passed"] is True
    assert gate["status"] == "ready_for_customer_acceptance"
    assert gate["minimum_commercial_gate_failures"] == []
    assert gate["violations"] == []
