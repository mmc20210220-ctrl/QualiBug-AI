from __future__ import annotations

import json

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_promotion_gate,
    _render_runtime_evidence_promotion_gate_markdown,
    run_grounded_probe_executor,
)


def _probe(candidate_id: str, path: str = "/orders") -> dict:
    return {
        "candidate_id": candidate_id,
        "risk_type": "auth_boundary_probe",
        "execution_policy": "read_only_safe",
        "endpoint": {"method": "GET", "path": path},
        "source_refs": [
            {"kind": "endpoint_contract", "source": "openapi.yaml"},
            {"kind": "business_requirement", "source": "prd.md"},
        ],
        "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1},
    }


def test_runtime_evidence_promotion_gate_approves_clean_customer_ready_run() -> None:
    report = {
        "project_id": "promotion-ready-demo",
        "runtime_evidence_scoreboard": {
            "execution_integrity_score": 96,
            "evidence_maturity": {"level": "customer_ready_runtime_evidence", "customer_ready": True},
        },
        "runtime_evidence_remediation_plan": {
            "p0_group_count": 0,
            "queued_candidate_count": 0,
            "priority_groups": [],
        },
        "runtime_customer_reproduction_pack": {
            "customer_ready_reproduction_count": 2,
            "blocked_reproduction_count": 0,
            "packages": [
                {
                    "candidate_id": "READY-1",
                    "customer_ready": True,
                    "reproduction_readiness_gate": {"customer_ready": True, "blockers": []},
                    "reproduction_trace": [{"phase": "target", "method": "GET", "path": "/orders/1", "status_code": 200}],
                },
                {
                    "candidate_id": "READY-2",
                    "customer_ready": True,
                    "reproduction_readiness_gate": {"customer_ready": True, "blockers": []},
                    "reproduction_trace": [{"phase": "target", "method": "GET", "path": "/orders/2", "status_code": 200}],
                },
            ],
        },
        "runtime_evidence_probe_ledger": {
            "customer_ready_probe_count": 2,
            "evidence_gap_probe_count": 0,
            "entries": [
                {"candidate_id": "READY-1", "customer_ready": True, "gap_types": []},
                {"candidate_id": "READY-2", "customer_ready": True, "gap_types": []},
            ],
        },
        "runtime_evidence_progress_delta": {
            "status": "customer_ready_runtime_evidence_progress",
            "regressions": [],
        },
        "runtime_evidence_carry_forward": {"blocked_candidate_count": 0},
        "runtime_rerun_selection": {"enabled": True, "missing_candidate_ids": []},
    }

    gate = _build_runtime_evidence_promotion_gate(report)

    assert gate["status"] == "customer_ready_runtime_evidence_promotion_approved"
    assert gate["promotion_ready"] is True
    assert gate["blockers"] == []
    assert gate["approved_customer_ready_candidate_ids"] == ["READY-1", "READY-2"]

    markdown = _render_runtime_evidence_promotion_gate_markdown(gate)
    assert "Runtime Evidence Promotion Gate" in markdown
    assert "customer_ready_runtime_evidence_promotion_approved" in markdown
    assert "READY-1" in markdown


def test_runtime_evidence_promotion_gate_blocks_regressions_and_remediation_queue() -> None:
    report = {
        "project_id": "promotion-blocked-demo",
        "runtime_evidence_scoreboard": {
            "execution_integrity_score": 54,
            "evidence_maturity": {"level": "runtime_evidence_blocked", "customer_ready": False},
        },
        "runtime_evidence_remediation_plan": {
            "p0_group_count": 2,
            "queued_candidate_count": 2,
        },
        "runtime_customer_reproduction_pack": {
            "customer_ready_reproduction_count": 1,
            "blocked_reproduction_count": 1,
            "packages": [
                {
                    "candidate_id": "READY",
                    "customer_ready": True,
                    "reproduction_readiness_gate": {"customer_ready": True, "blockers": []},
                    "reproduction_trace": [{"phase": "target", "path": "/orders/ready"}],
                },
                {
                    "candidate_id": "GAP",
                    "customer_ready": False,
                    "reproduction_readiness_gate": {"customer_ready": False, "blockers": ["missing_snapshot"]},
                    "reproduction_trace": [],
                },
            ],
        },
        "runtime_evidence_probe_ledger": {
            "customer_ready_probe_count": 1,
            "evidence_gap_probe_count": 1,
            "entries": [{"candidate_id": "GAP", "customer_ready": False, "gap_types": ["missing_snapshot"]}],
        },
        "runtime_evidence_progress_delta": {
            "status": "runtime_evidence_regression_detected",
            "regressions": ["execution_integrity_score_decreased"],
        },
        "runtime_evidence_carry_forward": {"blocked_candidate_count": 1},
        "runtime_rerun_selection": {"enabled": True, "missing_candidate_ids": ["MISSING"]},
    }

    gate = _build_runtime_evidence_promotion_gate(report)

    assert gate["promotion_ready"] is False
    assert gate["status"] == "runtime_evidence_promotion_blocked_by_regression"
    assert "p0_runtime_remediation_groups_remaining" in gate["blockers"]
    assert "runtime_evidence_progress_regression_detected" in gate["blockers"]
    assert "rerun_manifest_references_missing_candidates" in gate["blockers"]
    assert gate["blocked_candidate_ids"] == ["GAP", "MISSING"]


def test_executor_writes_runtime_evidence_promotion_gate_artifact(tmp_path) -> None:
    plan_path = tmp_path / "grounded_probe_plan.json"
    plan_path.write_text(json.dumps({"project_id": "promotion-output-demo", "probes": [_probe("DRY", "/orders/dry")]}), encoding="utf-8")
    out_dir = tmp_path / "out"

    report = run_grounded_probe_executor(probe_plan_path=plan_path, out_dir=out_dir)

    assert "runtime_evidence_promotion_gate" in report
    assert report["summary"]["runtime_promotion_gate_status"].startswith("runtime_evidence_promotion_blocked")
    assert report["summary"]["runtime_promotion_gate_ready"] is False
    assert (out_dir / "grounded_probe_runtime_evidence_promotion_gate.json").exists()
    assert (out_dir / "grounded_probe_runtime_evidence_promotion_gate.md").exists()
