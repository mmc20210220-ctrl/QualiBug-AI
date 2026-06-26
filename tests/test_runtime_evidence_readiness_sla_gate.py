from __future__ import annotations

from ai_test_asset_center.runtime_evidence_readiness_sla_gate import build_runtime_evidence_readiness_sla_gate


def _check(name: str, ok: bool) -> dict[str, object]:
    return {"name": name, "ok": ok}


def _ready_report(*, auth_ok: bool) -> dict[str, object]:
    return {
        "onboarding_preflight": {
            "ready_for_runtime": True,
            "ready_for_p0_p1_runtime_validation": True,
            "blocking_reasons": [],
            "warning_reasons": [] if auth_ok else ["auth_session_ready"],
            "checks": [
                _check("non_production_target", True),
                _check("base_url_configured", True),
                _check("probe_plan_grounded", True),
                _check("auth_session_ready", auth_ok),
            ],
        },
        "runtime_capability_matrix": {
            "rows": [
                {
                    "candidate_id": "P0-1",
                    "risk_type": "ownership_scope_probe",
                    "preflight_lane": "read_only_runtime_ready",
                    "expected_evidence_quality": "strong_runtime_before_after",
                    "high_value_runtime_risk": True,
                },
                {
                    "candidate_id": "P1-1",
                    "risk_type": "state_transition_probe",
                    "preflight_lane": "write_sandbox_runtime_ready",
                    "expected_evidence_quality": "strong_runtime_before_after",
                    "high_value_runtime_risk": True,
                },
            ]
        },
        "onboarding_remediation_kit": {"p0_action_count": 0, "p1_action_count": 0},
    }


def test_sla_gate_fails_when_auth_headers_are_configured_but_unverified() -> None:
    gate = build_runtime_evidence_readiness_sla_gate(_ready_report(auth_ok=False))

    assert gate["sla_gate_passed"] is False
    assert gate["commercial_readiness_level"] == "not_ready"
    assert gate["minimum_commercial_gate"]["auth_session_verified"] is False
    assert "auth_session_verified" in gate["minimum_commercial_gate_failures"]
    assert "auth_session_ready" in gate["commercial_blocking_reasons"]
    assert gate["commercial_readiness_score"] <= 49


def test_sla_gate_can_pass_after_auth_session_is_verified() -> None:
    gate = build_runtime_evidence_readiness_sla_gate(_ready_report(auth_ok=True))

    assert gate["sla_gate_passed"] is True
    assert gate["commercial_readiness_level"] == "commercial_ready"
    assert gate["minimum_commercial_gate"]["auth_session_verified"] is True
    assert gate["minimum_commercial_gate_failures"] == []
