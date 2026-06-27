from __future__ import annotations

import json

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_customer_delivery_manifest,
    _build_runtime_evidence_delivery_manifest_verification,
    _render_runtime_evidence_delivery_manifest_verification_markdown,
    run_grounded_probe_executor,
)


def _write_json(path, payload: dict) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _ready_report_with_delivery_artifacts(tmp_path) -> dict:
    outputs = {}
    artifact_payloads = {
        "runtime_evidence_scoreboard_json": {"engine": "scoreboard", "execution_integrity_score": 98},
        "runtime_evidence_probe_ledger_json": {"engine": "ledger", "customer_ready_probe_count": 1},
        "runtime_customer_reproduction_pack_json": {"engine": "pack", "customer_ready_reproduction_count": 1},
        "runtime_evidence_remediation_plan_json": {"engine": "remediation", "p0_group_count": 0},
        "runtime_evidence_carry_forward_json": {"engine": "carry", "carried_forward_reproduction_count": 0},
        "runtime_evidence_progress_delta_json": {"engine": "delta", "status": "customer_ready_runtime_evidence_progress"},
        "runtime_evidence_promotion_gate_json": {"engine": "promotion", "promotion_ready": True},
    }
    for key, payload in artifact_payloads.items():
        outputs[key] = _write_json(tmp_path / f"{key}.json", payload)

    return {
        "project_id": "delivery-verification-demo",
        "created_at": "2026-06-27T00:00:00Z",
        "outputs": outputs,
        "runtime_evidence_promotion_gate": {
            "status": "customer_ready_runtime_evidence_promotion_approved",
            "promotion_ready": True,
            "blockers": [],
            "approved_customer_ready_candidate_ids": ["READY-1"],
            "blocked_candidate_ids": [],
        },
        "runtime_customer_reproduction_pack": {"customer_ready_reproduction_count": 1},
        "runtime_evidence_probe_ledger": {"customer_ready_probe_count": 1},
        "runtime_evidence_progress_delta": {"status": "customer_ready_runtime_evidence_progress", "regressions": []},
    }


def test_delivery_manifest_verification_rehashes_and_approves_intact_manifest(tmp_path) -> None:
    report = _ready_report_with_delivery_artifacts(tmp_path)
    manifest = _build_runtime_evidence_customer_delivery_manifest(report)
    report["runtime_evidence_customer_delivery_manifest"] = manifest

    verification = _build_runtime_evidence_delivery_manifest_verification({}, report)

    assert verification["status"] == "runtime_delivery_manifest_verified"
    assert verification["verified"] is True
    assert verification["baseline_id_matches"] is True
    assert verification["failed_required_artifact_count"] == 0
    assert verification["verified_artifact_count"] == manifest["artifact_count"]
    assert verification["approved_customer_ready_candidate_ids"] == ["READY-1"]

    markdown = _render_runtime_evidence_delivery_manifest_verification_markdown(verification)
    assert "Runtime Evidence Delivery Manifest Verification" in markdown
    assert "runtime_delivery_manifest_verified" in markdown
    assert "READY-1" in markdown


def test_delivery_manifest_verification_detects_required_artifact_drift(tmp_path) -> None:
    report = _ready_report_with_delivery_artifacts(tmp_path)
    manifest = _build_runtime_evidence_customer_delivery_manifest(report)
    report["runtime_evidence_customer_delivery_manifest"] = manifest

    # Tamper with a required artifact after the manifest froze its SHA256.
    ledger_path = tmp_path / "runtime_evidence_probe_ledger_json.json"
    ledger_path.write_text(json.dumps({"engine": "ledger", "tampered": True}), encoding="utf-8")

    verification = _build_runtime_evidence_delivery_manifest_verification({}, report)

    assert verification["verified"] is False
    assert verification["status"] == "runtime_delivery_manifest_verification_failed"
    assert "required_delivery_artifact_hash_verification_failed" in verification["blockers"]
    assert verification["failed_required_artifact_count"] == 1
    failed = verification["failed_required_artifacts"][0]
    assert failed["artifact_key"] == "runtime_evidence_probe_ledger_json"
    assert "sha256_mismatch" in failed["issues"]
    assert verification["tamper_evident"] is True


def test_delivery_manifest_verification_can_load_configured_previous_manifest(tmp_path) -> None:
    report = _ready_report_with_delivery_artifacts(tmp_path)
    manifest = _build_runtime_evidence_customer_delivery_manifest(report)
    manifest_path = tmp_path / "previous_delivery_manifest.json"
    _write_json(manifest_path, manifest)

    verification = _build_runtime_evidence_delivery_manifest_verification(
        {"previous_runtime_evidence_customer_delivery_manifest_path": str(manifest_path)},
        {"project_id": "next-run"},
    )

    assert verification["verified"] is True
    assert verification["source"] == "configured_manifest_path"
    assert verification["manifest_path"] == str(manifest_path)
    assert verification["delivery_baseline_id"] == manifest["delivery_baseline_id"]


def test_executor_writes_delivery_manifest_verification_artifact(tmp_path) -> None:
    plan_path = tmp_path / "grounded_probe_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "project_id": "delivery-verification-output-demo",
                "probes": [
                    {
                        "candidate_id": "DRY",
                        "risk_type": "auth_boundary_probe",
                        "execution_policy": "read_only_safe",
                        "endpoint": {"method": "GET", "path": "/orders/dry"},
                        "source_refs": [
                            {"kind": "endpoint_contract", "source": "openapi.yaml"},
                            {"kind": "business_requirement", "source": "prd.md"},
                        ],
                        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    report = run_grounded_probe_executor(probe_plan_path=plan_path, out_dir=out_dir)

    assert "runtime_evidence_delivery_manifest_verification" in report
    assert report["summary"]["runtime_delivery_manifest_verification_status"].startswith("runtime_delivery_manifest_verification_")
    assert report["summary"]["runtime_delivery_manifest_verified"] is False
    assert (out_dir / "grounded_probe_runtime_evidence_delivery_manifest_verification.json").exists()
    assert (out_dir / "grounded_probe_runtime_evidence_delivery_manifest_verification.md").exists()
