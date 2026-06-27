from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.runtime_commercial_handoff_bundle import build_commercial_handoff_bundle
from ai_test_asset_center.runtime_handoff_archive_manifest import build_handoff_archive_manifest
from ai_test_asset_center.runtime_handoff_secret_audit import audit_commercial_handoff_secrets, render_handoff_secret_audit_markdown


def _base_report(tmp_path: Path | None = None) -> dict:
    report = {
        "project_id": "demo",
        "created_at": "2026-06-27T00:00:00Z",
        "summary": {"validated_candidate_count": 1, "runtime_evidence_readiness_score": 98},
        "runtime_evidence_readiness_sla_gate": {
            "commercial_readiness_score": 98,
            "commercial_readiness_level": "commercial_ready",
            "sla_gate_passed": True,
            "minimum_commercial_gate_failures": [],
            "commercial_blocking_reasons": [],
        },
        "runtime_sla_execution_policy": {"status": "ready", "must_run_for_sla_count": 0, "blocked_before_sla_count": 0},
        "runtime_sla_gap_prioritizer": {"action_count": 0},
        "onboarding_patch_safety_validation": {"status": "safe_to_send", "safe_to_send_to_customer": True},
        "write_sandbox_approval_packet": {"write_approval_required": False},
        "onboarding_preflight": {"status": "ready"},
        "runtime_evidence_promotion_gate": {
            "status": "customer_ready_runtime_evidence_promotion_approved",
            "promotion_ready": True,
            "blockers": [],
            "approved_customer_ready_candidate_ids": ["READY-1"],
        },
        "runtime_evidence_customer_delivery_manifest": {
            "status": "customer_ready_runtime_delivery_manifest_ready",
            "customer_ready": True,
            "delivery_baseline_id": "qbruntime-ready",
            "approved_customer_ready_candidate_ids": ["READY-1"],
        },
        "runtime_evidence_delivery_manifest_verification": {
            "status": "runtime_delivery_manifest_verified",
            "verified": True,
            "blockers": [],
        },
        "runtime_customer_reproduction_pack": {
            "packages": [
                {
                    "candidate_id": "READY-1",
                    "customer_ready": True,
                    "curl_commands": ["curl -H 'Authorization: <REDACTED>' $BASE_URL/orders/1"],
                }
            ]
        },
        "runtime_evidence_probe_ledger": {"entries": [{"candidate_id": "READY-1", "customer_ready": True}]},
        "findings": [{"finding_id": "GPF-1", "priority": "P1"}],
        "outputs": {},
    }
    if tmp_path is not None:
        artifact = tmp_path / "runtime.json"
        artifact.write_text('{"ok": true}', encoding="utf-8")
        report["outputs"] = {
            "runtime_evidence_promotion_gate_json": str(artifact),
            "runtime_evidence_customer_delivery_manifest_json": str(artifact),
            "runtime_evidence_delivery_manifest_verification_json": str(artifact),
        }
    return report


def test_secret_audit_scans_phase95_runtime_evidence_and_blocks_raw_tokens() -> None:
    report = _base_report()
    report["runtime_customer_reproduction_pack"]["packages"][0]["curl_commands"] = [
        "curl -H 'Authorization: Bearer abcdefghijklmnopqrstuvwx' $BASE_URL/orders/1"
    ]

    audit = audit_commercial_handoff_secrets(report)

    assert audit["status"] == "handoff_secret_audit_blocked"
    assert audit["safe_for_customer_handoff"] is False
    assert audit["runtime_evidence_issue_count"] == 1
    assert "runtime_customer_reproduction_pack" in audit["scanned_runtime_evidence_sections"]
    assert audit["issues"][0]["issue_id"] == "HANDOFF-RAW-SECRET-VALUE"
    assert "runtime_customer_reproduction_pack" in audit["issues"][0]["path"]


def test_secret_audit_allows_redacted_runtime_evidence_placeholders() -> None:
    report = _base_report()
    report["runtime_customer_reproduction_pack"]["packages"][0]["headers"] = {
        "Authorization": "<REDACTED>",
        "Cookie": "***",
        "token_header_name": "Authorization",
    }

    audit = audit_commercial_handoff_secrets(report)
    md = render_handoff_secret_audit_markdown(audit)

    assert audit["status"] == "handoff_secret_audit_passed"
    assert audit["safe_for_customer_handoff"] is True
    assert audit["runtime_evidence_issue_count"] == 0
    assert "scanned runtime evidence sections" in md


def test_handoff_bundle_blocks_when_runtime_evidence_secret_audit_fails() -> None:
    report = _base_report()
    report["commercial_handoff_secret_audit"] = {
        "status": "handoff_secret_audit_blocked",
        "safe_for_customer_handoff": False,
        "issue_count": 1,
        "runtime_evidence_issue_count": 1,
        "issues": [{"issue_id": "HANDOFF-RAW-SECRET-VALUE"}],
    }

    bundle = build_commercial_handoff_bundle(report)

    assert bundle["status"] == "handoff_blocked_by_secret_audit"
    summary = bundle["executive_summary"]
    assert summary["commercial_handoff_safe_for_customer"] is False
    assert summary["runtime_evidence_secret_issue_count"] == 1
    item = next(x for x in bundle["customer_signoff_checklist"] if x["item_id"] == "HANDOFF-SECRET-AUDIT")
    assert item["required"] is True
    assert item["passed"] is False
    assert item["runtime_evidence_issue_count"] == 1


def test_archive_manifest_is_blocked_when_runtime_evidence_secret_audit_fails(tmp_path: Path) -> None:
    report = _base_report(tmp_path)
    report["commercial_handoff_secret_audit"] = {
        "status": "handoff_secret_audit_blocked",
        "safe_for_customer_handoff": False,
        "issue_count": 2,
        "runtime_evidence_issue_count": 2,
        "issues": [],
    }
    report["commercial_handoff_bundle"] = {"status": "handoff_blocked_by_secret_audit"}
    report["commercial_handoff_acceptance_gate"] = {"status": "blocked", "acceptance_gate_passed": False}

    manifest = build_handoff_archive_manifest(report)

    assert manifest["status"] == "archive_receipt_blocked_by_secret_audit"
    assert manifest["secret_audit_blocked"] is True
    receipt = manifest["immutable_run_receipt"]
    assert receipt["receipt_status"] == "archive_receipt_blocked_by_secret_audit"
    assert receipt["safe_for_customer_handoff"] is False
    assert receipt["runtime_evidence_secret_issue_count"] == 2
