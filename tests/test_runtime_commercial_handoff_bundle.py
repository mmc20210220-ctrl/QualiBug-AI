from __future__ import annotations

from ai_test_asset_center.runtime_commercial_handoff_bundle import build_commercial_handoff_bundle


def test_handoff_bundle_surfaces_minimum_commercial_gate_failures() -> None:
    bundle = build_commercial_handoff_bundle(
        {
            "project_id": "demo",
            "summary": {"validated_candidate_count": 0},
            "runtime_evidence_readiness_sla_gate": {
                "commercial_readiness_score": 49,
                "commercial_readiness_level": "not_ready",
                "sla_gate_passed": False,
                "minimum_commercial_gate_failures": ["auth_session_verified"],
                "commercial_blocking_reasons": ["auth_session_ready"],
            },
            "runtime_sla_gap_prioritizer": {"action_count": 1},
            "runtime_sla_execution_policy": {"must_run_for_sla_count": 2, "blocked_before_sla_count": 0},
            "onboarding_patch_safety_validation": {"status": "safe_to_send", "safe_to_send_to_customer": True},
            "write_sandbox_approval_packet": {"write_approval_required": False},
            "onboarding_preflight": {"status": "degraded"},
            "findings": [],
        }
    )

    summary = bundle["executive_summary"]
    minimum_item = next(item for item in bundle["customer_signoff_checklist"] if item["item_id"] == "HANDOFF-MINIMUM-COMMERCIAL-GATE")
    sla_item = next(item for item in bundle["customer_signoff_checklist"] if item["item_id"] == "HANDOFF-SLA-GATE")

    assert bundle["status"] == "conditional_handoff_onboarding_delta_required"
    assert summary["minimum_commercial_gate_failures"] == ["auth_session_verified"]
    assert summary["commercial_blocking_reasons"] == ["auth_session_ready"]
    assert minimum_item["required"] is True
    assert minimum_item["passed"] is False
    assert minimum_item["minimum_gate_failures"] == ["auth_session_verified"]
    assert sla_item["minimum_gate_failures"] == ["auth_session_verified"]
    assert summary["handoff_blocker_count"] == 2


def test_handoff_bundle_minimum_gate_item_passes_when_failures_are_empty() -> None:
    bundle = build_commercial_handoff_bundle(
        {
            "project_id": "demo",
            "summary": {"validated_candidate_count": 0},
            "runtime_evidence_readiness_sla_gate": {
                "commercial_readiness_score": 92,
                "commercial_readiness_level": "commercial_ready",
                "sla_gate_passed": True,
                "minimum_commercial_gate_failures": [],
                "commercial_blocking_reasons": [],
            },
            "runtime_sla_execution_policy": {"must_run_for_sla_count": 2, "blocked_before_sla_count": 0},
            "onboarding_patch_safety_validation": {"status": "safe_to_send", "safe_to_send_to_customer": True},
            "write_sandbox_approval_packet": {"write_approval_required": False},
            "onboarding_preflight": {"status": "ready"},
            "findings": [],
        }
    )

    minimum_item = next(item for item in bundle["customer_signoff_checklist"] if item["item_id"] == "HANDOFF-MINIMUM-COMMERCIAL-GATE")

    assert bundle["status"] == "commercial_handoff_ready_no_validated_findings"
    assert minimum_item["passed"] is True
    assert bundle["executive_summary"]["handoff_blocker_count"] == 0
