from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.runtime_commercial_handoff_bundle import build_commercial_handoff_bundle
from ai_test_asset_center.runtime_handoff_archive_manifest import build_handoff_archive_manifest
from ai_test_asset_center.runtime_handoff_secret_audit import (
    audit_commercial_handoff_secrets,
    build_handoff_redacted_runtime_evidence_pack,
    build_handoff_secret_redaction_plan,
    render_handoff_redacted_runtime_evidence_markdown,
)


def _report() -> dict:
    return {
        "project_id": "demo",
        "created_at": "2026-06-27T00:00:00Z",
        "summary": {"runtime_evidence_readiness_score": 98},
        "runtime_evidence_readiness_sla_gate": {
            "commercial_readiness_score": 98,
            "commercial_readiness_level": "commercial_ready",
            "sla_gate_passed": True,
            "minimum_commercial_gate_failures": [],
            "commercial_blocking_reasons": [],
        },
        "runtime_evidence_promotion_gate": {
            "status": "customer_ready_runtime_evidence_promotion_approved",
            "promotion_ready": True,
            "blockers": [],
            "approved_customer_ready_candidate_ids": ["READY"],
        },
        "runtime_evidence_customer_delivery_manifest": {
            "status": "customer_ready_runtime_delivery_manifest_ready",
            "customer_ready": True,
        },
        "runtime_evidence_delivery_manifest_verification": {"verified": True},
        "runtime_sla_gap_prioritizer": {"action_count": 0},
        "onboarding_patch_safety_validation": {"status": "safe_to_send", "safe_to_send_to_customer": True},
        "write_sandbox_approval_packet": {"write_approval_required": False},
        "runtime_customer_reproduction_pack": {
            "packages": [
                {
                    "candidate_id": "AUTH-LEAK",
                    "customer_ready": True,
                    "reproduction_trace": [
                        {
                            "phase": "target",
                            "curl_template": "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwx' $BASE_URL/orders/1",
                        }
                    ],
                }
            ]
        },
        "runtime_evidence_probe_ledger": {"entries": [{"candidate_id": "AUTH-LEAK", "customer_ready": True}]},
        "findings": [{"finding_id": "GPF-1", "candidate_id": "AUTH-LEAK", "priority": "P0"}],
        "outputs": {},
    }


def test_auto_redaction_generates_customer_safe_runtime_evidence_copy_without_mutating_original() -> None:
    report = _report()
    audit = audit_commercial_handoff_secrets(report)
    plan = build_handoff_secret_redaction_plan(report, audit)

    pack = build_handoff_redacted_runtime_evidence_pack(report, audit, plan)
    md = render_handoff_redacted_runtime_evidence_markdown(pack)
    serialized = json.dumps(pack, ensure_ascii=False)

    assert audit["status"] == "handoff_secret_audit_blocked"
    assert pack["status"] == "handoff_redacted_runtime_evidence_ready"
    assert pack["safe_for_customer_handoff_after_redaction"] is True
    assert pack["applied_action_count"] == 1
    assert pack["skipped_action_count"] == 0
    assert pack["verification_audit"]["status"] == "handoff_secret_audit_passed"
    assert "runtime_customer_reproduction_pack" in pack["redacted_runtime_evidence_sections"]
    assert "Bearer abcdefghijklmnopqrstuvwx" not in serialized
    assert "<REDACTED_RUNTIME_SECRET>" in serialized
    assert "AUTH-LEAK" in serialized
    assert "Applied redactions" in md

    # Original source evidence stays untouched so the audit trail remains honest.
    original_trace = report["runtime_customer_reproduction_pack"]["packages"][0]["reproduction_trace"][0]
    assert "Bearer abcdefghijklmnopqrstuvwx" in original_trace["curl_template"]


def test_auto_redaction_pack_is_not_required_when_secret_audit_passes() -> None:
    report = _report()
    report["runtime_customer_reproduction_pack"]["packages"][0]["reproduction_trace"][0]["curl_template"] = (
        "curl -H 'Authorization: <REDACTED>' $BASE_URL/orders/1"
    )
    audit = audit_commercial_handoff_secrets(report)

    pack = build_handoff_redacted_runtime_evidence_pack(report, audit)

    assert audit["safe_for_customer_handoff"] is True
    assert pack["status"] == "handoff_redacted_runtime_evidence_not_required"
    assert pack["redaction_applied"] is False
    assert pack["safe_for_customer_handoff_after_redaction"] is True
    assert pack["applied_action_count"] == 0


def test_handoff_and_archive_include_redacted_runtime_evidence_artifact_when_redaction_is_required(tmp_path: Path) -> None:
    report = _report()
    audit = audit_commercial_handoff_secrets(report)
    plan = build_handoff_secret_redaction_plan(report, audit)
    pack = build_handoff_redacted_runtime_evidence_pack(report, audit, plan)

    redacted_file = tmp_path / "grounded_probe_commercial_handoff_redacted_runtime_evidence.json"
    redacted_file.write_text(json.dumps(pack), encoding="utf-8")
    redaction_file = tmp_path / "grounded_probe_commercial_handoff_secret_redaction_plan.json"
    redaction_file.write_text(json.dumps(plan), encoding="utf-8")
    execution_file = tmp_path / "grounded_probe_execution_report.json"
    execution_file.write_text("{}", encoding="utf-8")

    report["outputs"] = {
        "execution_report": str(execution_file),
        "commercial_handoff_secret_redaction_plan_json": str(redaction_file),
        "commercial_handoff_redacted_runtime_evidence_json": str(redacted_file),
    }
    report["commercial_handoff_secret_audit"] = audit
    report["commercial_handoff_secret_redaction_plan"] = plan
    report["commercial_handoff_redacted_runtime_evidence"] = pack
    report["commercial_handoff_bundle"] = {"status": "handoff_blocked_by_secret_audit"}
    report["commercial_handoff_acceptance_gate"] = {"status": "blocked", "acceptance_gate_passed": False}

    bundle = build_commercial_handoff_bundle(report)
    manifest = build_handoff_archive_manifest(report)

    handoff_entry = next(
        item for item in bundle["artifact_manifest"] if item["artifact_key"] == "commercial_handoff_redacted_runtime_evidence_json"
    )
    archive_entry = next(
        item for item in manifest["artifact_manifest"] if item["artifact_key"] == "commercial_handoff_redacted_runtime_evidence_json"
    )

    assert handoff_entry["required_for_handoff"] is True
    assert archive_entry["required_for_archive"] is True
    assert archive_entry["sha256"]
    assert manifest["immutable_run_receipt"]["commercial_handoff_redacted_runtime_evidence_hash"]
    assert "commercial_handoff_redacted_runtime_evidence_hash" in manifest["comparison_keys_for_future_reruns"]
