from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.runtime_commercial_handoff_bundle import build_commercial_handoff_bundle
from ai_test_asset_center.runtime_handoff_archive_manifest import build_handoff_archive_manifest


def _ready_report() -> dict:
    return {
        "project_id": "demo",
        "created_at": "2026-06-27T00:00:00Z",
        "summary": {"validated_candidate_count": 1},
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
        "findings": [{"finding_id": "GPF-1", "priority": "P1"}],
        "outputs": {},
    }


def test_handoff_bundle_blocks_when_runtime_delivery_manifest_verification_fails() -> None:
    report = _ready_report()
    report["runtime_evidence_delivery_manifest_verification"] = {
        "status": "runtime_delivery_manifest_verification_failed",
        "verified": False,
        "blockers": ["required_delivery_artifact_hash_verification_failed"],
    }

    bundle = build_commercial_handoff_bundle(report)

    assert bundle["status"] == "handoff_blocked_by_runtime_delivery_manifest_verification"
    summary = bundle["executive_summary"]
    assert summary["runtime_promotion_gate_ready"] is True
    assert summary["runtime_delivery_manifest_ready"] is True
    assert summary["runtime_delivery_manifest_verified"] is False
    assert summary["handoff_blocker_count"] == 1
    verify_item = next(item for item in bundle["customer_signoff_checklist"] if item["item_id"] == "HANDOFF-RUNTIME-MANIFEST-VERIFY")
    assert verify_item["required"] is True
    assert verify_item["passed"] is False
    assert verify_item["verification_blockers"] == ["required_delivery_artifact_hash_verification_failed"]


def test_handoff_bundle_surfaces_runtime_delivery_artifacts_as_required_handoff_items() -> None:
    report = _ready_report()
    report["outputs"] = {
        "runtime_evidence_promotion_gate_json": "/tmp/promotion.json",
        "runtime_evidence_customer_delivery_manifest_json": "/tmp/delivery.json",
        "runtime_evidence_delivery_manifest_verification_json": "/tmp/verification.json",
    }

    bundle = build_commercial_handoff_bundle(report)
    artifacts = {item["artifact_key"]: item for item in bundle["artifact_manifest"]}

    assert bundle["status"] == "commercial_handoff_ready_with_validated_findings"
    assert artifacts["runtime_evidence_promotion_gate_json"]["required_for_handoff"] is True
    assert artifacts["runtime_evidence_customer_delivery_manifest_json"]["required_for_handoff"] is True
    assert artifacts["runtime_evidence_delivery_manifest_verification_json"]["required_for_handoff"] is True
    assert artifacts["runtime_evidence_delivery_manifest_verification_json"]["path"] == "/tmp/verification.json"


def test_archive_manifest_marks_runtime_delivery_artifacts_required_and_phase95(tmp_path: Path) -> None:
    promotion = tmp_path / "promotion.json"
    delivery = tmp_path / "delivery.json"
    verification = tmp_path / "verification.json"
    for path in (promotion, delivery, verification):
        path.write_text('{"ok": true}', encoding="utf-8")

    report = _ready_report()
    report["outputs"] = {
        "runtime_evidence_promotion_gate_json": str(promotion),
        "runtime_evidence_customer_delivery_manifest_json": str(delivery),
        "runtime_evidence_delivery_manifest_verification_json": str(verification),
    }

    manifest = build_handoff_archive_manifest(report)
    entries = {item["artifact_key"]: item for item in manifest["artifact_manifest"]}

    assert entries["runtime_evidence_promotion_gate_json"]["required_for_archive"] is True
    assert entries["runtime_evidence_customer_delivery_manifest_json"]["phase"] == "phase95_runtime_delivery"
    assert entries["runtime_evidence_delivery_manifest_verification_json"]["sha256"]
    receipt = manifest["immutable_run_receipt"]
    assert receipt["runtime_evidence_promotion_gate_hash"]
    assert receipt["runtime_evidence_customer_delivery_manifest_hash"]
    assert receipt["runtime_evidence_delivery_manifest_verification_hash"]
    assert "runtime_evidence_delivery_manifest_verification_hash" in manifest["comparison_keys_for_future_reruns"]
