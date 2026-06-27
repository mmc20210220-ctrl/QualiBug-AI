from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.runtime_commercial_handoff_bundle import build_commercial_handoff_bundle
from ai_test_asset_center.runtime_handoff_archive_manifest import build_handoff_archive_manifest
from ai_test_asset_center.runtime_handoff_secret_audit import (
    audit_commercial_handoff_secrets,
    build_handoff_secret_redaction_plan,
    render_handoff_secret_redaction_plan_markdown,
)


def _report() -> dict:
    return {
        "project_id": "demo",
        "created_at": "2026-06-27T00:00:00Z",
        "summary": {"runtime_evidence_readiness_score": 98},
        "runtime_evidence_readiness_sla_gate": {
            "commercial_readiness_score": 98,
            "sla_gate_passed": True,
            "minimum_commercial_gate_failures": [],
            "commercial_blocking_reasons": [],
        },
        "runtime_evidence_promotion_gate": {"promotion_ready": True, "approved_customer_ready_candidate_ids": ["READY"]},
        "runtime_evidence_customer_delivery_manifest": {"customer_ready": True},
        "runtime_evidence_delivery_manifest_verification": {"verified": True},
        "runtime_sla_gap_prioritizer": {"action_count": 0},
        "onboarding_patch_safety_validation": {"status": "safe_to_send", "safe_to_send_to_customer": True},
        "write_sandbox_approval_packet": {"write_approval_required": False},
        "runtime_customer_reproduction_pack": {
            "packages": [
                {
                    "candidate_id": "AUTH-LEAK",
                    "customer_ready": True,
                    "curl_commands": ["curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwx' $BASE_URL/orders/1"],
                }
            ]
        },
        "runtime_evidence_probe_ledger": {"entries": [{"candidate_id": "AUTH-LEAK", "readiness_level": "customer_ready_candidate"}]},
        "findings": [{"finding_id": "GPF-1", "candidate_id": "AUTH-LEAK", "priority": "P0"}],
        "outputs": {},
    }


def test_secret_redaction_plan_turns_audit_issues_into_p0_runtime_actions() -> None:
    report = _report()
    audit = audit_commercial_handoff_secrets(report)

    plan = build_handoff_secret_redaction_plan(report, audit)
    md = render_handoff_secret_redaction_plan_markdown(plan)

    assert audit["status"] == "handoff_secret_audit_blocked"
    assert plan["status"] == "handoff_secret_redaction_required"
    assert plan["redaction_required"] is True
    assert plan["action_count"] == 1
    assert plan["p0_action_count"] == 1
    assert plan["affected_runtime_evidence_sections"] == ["runtime_customer_reproduction_pack"]
    action = plan["redaction_actions"][0]
    assert action["priority"] == "P0"
    assert action["replacement"] == "<REDACTED_RUNTIME_SECRET>"
    assert action["blocks_customer_handoff"] is True
    assert "rerun_commercial_handoff_secret_audit" in action["required_follow_up"]
    assert "REDACT-001" in md
    assert "runtime_customer_reproduction_pack" in md


def test_secret_redaction_plan_is_not_required_when_audit_passes() -> None:
    report = _report()
    report["runtime_customer_reproduction_pack"]["packages"][0]["curl_commands"] = [
        "curl -H 'Authorization: <REDACTED>' $BASE_URL/orders/1"
    ]
    audit = audit_commercial_handoff_secrets(report)

    plan = build_handoff_secret_redaction_plan(report, audit)

    assert audit["safe_for_customer_handoff"] is True
    assert plan["status"] == "handoff_secret_redaction_not_required"
    assert plan["action_count"] == 0
    assert plan["safe_for_customer_handoff_after_regeneration"] is True


def test_commercial_handoff_lists_secret_redaction_plan_and_blocks_on_audit() -> None:
    report = _report()
    report["outputs"] = {
        "commercial_handoff_secret_redaction_plan_json": "grounded_probe_commercial_handoff_secret_redaction_plan.json",
        "commercial_handoff_secret_redaction_plan_md": "grounded_probe_commercial_handoff_secret_redaction_plan.md",
    }
    report["commercial_handoff_secret_audit"] = audit_commercial_handoff_secrets(report)
    report["commercial_handoff_secret_redaction_plan"] = build_handoff_secret_redaction_plan(
        report, report["commercial_handoff_secret_audit"]
    )

    bundle = build_commercial_handoff_bundle(report)

    assert bundle["status"] == "handoff_blocked_by_secret_audit"
    assert bundle["executive_summary"]["secret_redaction_action_count"] == 1
    keys = {item["artifact_key"] for item in bundle["artifact_manifest"]}
    assert "commercial_handoff_secret_redaction_plan_json" in keys


def test_archive_manifest_hashes_secret_redaction_plan_for_future_reruns(tmp_path: Path) -> None:
    report = _report()
    redaction_file = tmp_path / "grounded_probe_commercial_handoff_secret_redaction_plan.json"
    redaction_file.write_text('{"status":"handoff_secret_redaction_required"}', encoding="utf-8")
    report["outputs"] = {
        "execution_report": str(tmp_path / "execution.json"),
        "commercial_handoff_secret_audit_json": str(tmp_path / "audit.json"),
        "commercial_handoff_secret_redaction_plan_json": str(redaction_file),
    }
    Path(report["outputs"]["execution_report"]).write_text("{}", encoding="utf-8")
    Path(report["outputs"]["commercial_handoff_secret_audit_json"]).write_text("{}", encoding="utf-8")
    report["commercial_handoff_secret_audit"] = audit_commercial_handoff_secrets(report)
    report["commercial_handoff_secret_redaction_plan"] = build_handoff_secret_redaction_plan(
        report, report["commercial_handoff_secret_audit"]
    )
    report["commercial_handoff_bundle"] = {"status": "handoff_blocked_by_secret_audit"}
    report["commercial_handoff_acceptance_gate"] = {"status": "blocked", "acceptance_gate_passed": False}

    manifest = build_handoff_archive_manifest(report)

    redaction_entry = next(
        item for item in manifest["artifact_manifest"] if item["artifact_key"] == "commercial_handoff_secret_redaction_plan_json"
    )
    assert redaction_entry["required_for_archive"] is True
    assert redaction_entry["sha256"]
    receipt = manifest["immutable_run_receipt"]
    assert receipt["commercial_handoff_secret_redaction_plan_hash"]
    assert receipt["secret_redaction_action_count"] == 1
    assert "commercial_handoff_secret_redaction_plan_hash" in manifest["comparison_keys_for_future_reruns"]
