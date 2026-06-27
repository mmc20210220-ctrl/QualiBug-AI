from __future__ import annotations

import json

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_customer_delivery_manifest,
    _render_runtime_evidence_customer_delivery_manifest_markdown,
    run_grounded_probe_executor,
)


def _write_json(path, payload: dict) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _ready_report_with_artifacts(tmp_path) -> dict:
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
        "project_id": "delivery-ready-demo",
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


def test_runtime_delivery_manifest_hashes_required_artifacts_and_freezes_baseline(tmp_path) -> None:
    report = _ready_report_with_artifacts(tmp_path)

    manifest = _build_runtime_evidence_customer_delivery_manifest(report)

    assert manifest["status"] == "customer_ready_runtime_delivery_manifest_ready"
    assert manifest["customer_ready"] is True
    assert manifest["delivery_baseline_id"].startswith("qbruntime-")
    assert manifest["approved_customer_ready_candidate_ids"] == ["READY-1"]
    assert manifest["hashed_required_artifact_count"] == manifest["required_artifact_count"] == 7
    assert manifest["missing_required_artifact_count"] == 0
    assert all(entry["sha256"] for entry in manifest["artifact_manifest"] if entry["required"])

    markdown = _render_runtime_evidence_customer_delivery_manifest_markdown(manifest)
    assert "Runtime Evidence Customer Delivery Manifest" in markdown
    assert "customer_ready_runtime_delivery_manifest_ready" in markdown
    assert "READY-1" in markdown
    assert "SHA256" in markdown


def test_runtime_delivery_manifest_blocks_when_promotion_not_ready_or_artifacts_missing(tmp_path) -> None:
    report = _ready_report_with_artifacts(tmp_path)
    report["runtime_evidence_promotion_gate"]["promotion_ready"] = False
    report["runtime_evidence_promotion_gate"]["status"] = "runtime_evidence_promotion_blocked"
    report["runtime_evidence_promotion_gate"]["blockers"] = ["probe_ledger_evidence_gaps_remaining"]
    report["outputs"]["runtime_evidence_probe_ledger_json"] = str(tmp_path / "missing-ledger.json")

    manifest = _build_runtime_evidence_customer_delivery_manifest(report)

    assert manifest["customer_ready"] is False
    assert manifest["status"] == "runtime_delivery_manifest_missing_required_artifacts"
    assert "runtime_promotion_gate_not_approved" in manifest["blockers"]
    assert "required_runtime_delivery_artifacts_missing_or_unhashable" in manifest["blockers"]
    assert manifest["missing_required_artifact_count"] == 1
    assert manifest["missing_required_artifacts"][0]["artifact_key"] == "runtime_evidence_probe_ledger_json"


def test_executor_writes_runtime_delivery_manifest_artifact(tmp_path) -> None:
    plan_path = tmp_path / "grounded_probe_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "project_id": "delivery-output-demo",
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

    assert "runtime_evidence_customer_delivery_manifest" in report
    assert report["summary"]["runtime_delivery_manifest_status"].startswith("runtime_delivery_manifest_")
    assert report["summary"]["runtime_delivery_manifest_ready"] is False
    assert (out_dir / "grounded_probe_runtime_evidence_customer_delivery_manifest.json").exists()
    assert (out_dir / "grounded_probe_runtime_evidence_customer_delivery_manifest.md").exists()
