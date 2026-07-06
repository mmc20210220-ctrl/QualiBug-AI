from __future__ import annotations

from ai_test_asset_center.release_gate import evaluate_release_gate


def _ready_inputs():
    return {
        "campaign": {"campaign_id": "CMP_1", "campaign_status": "completed"},
        "execution_status": "completed",
        "runtime_contract": {"status": "approved"},
        "evidence_bundle": {"status": "persisted", "bundle_id": "evb_1"},
        "evidence_bundle_verification": {"valid": True},
        "test_data_plan": {"status": "ready"},
        "findings": [],
        "coverage_gaps": [],
    }


def test_release_gate_passes_only_when_all_governance_requirements_are_closed():
    decision = evaluate_release_gate(**_ready_inputs())
    assert decision["verdict"] == "pass"
    assert decision["status"] == "release_ready"


def test_release_gate_blocks_campaign_deferred_or_p0():
    deferred = _ready_inputs()
    deferred["campaign"] = {"campaign_id": "CMP_1", "campaign_status": "coverage_deferred", "coverage_deferred_reason": "configured_round_limit_reached"}
    decision = evaluate_release_gate(**deferred)
    assert decision["verdict"] == "fail"
    assert any(reason["code"] == "CAMPAIGN_NOT_CLOSED" for reason in decision["reasons"])

    p0 = _ready_inputs()
    p0["findings"] = [{"severity": "P0", "confirmation_status": "confirmed"}]
    decision = evaluate_release_gate(**p0)
    assert decision["verdict"] == "fail"
    assert any(reason["code"] == "CONFIRMED_P0_FINDINGS" for reason in decision["reasons"])


def test_release_gate_never_passes_without_verifiable_evidence_or_execution():
    pending = _ready_inputs()
    pending["execution_status"] = "plan_only"
    pending["evidence_bundle_verification"] = {"valid": False, "code": "EVIDENCE_ARTIFACT_HASH_MISMATCH"}
    decision = evaluate_release_gate(**pending)
    assert decision["verdict"] == "not_ready"
    assert decision["status"] == "inconclusive"
    assert {reason["code"] for reason in decision["reasons"]}.issuperset({"RUNTIME_EXECUTION_NOT_COMPLETED", "EVIDENCE_BUNDLE_NOT_VERIFIED"})
