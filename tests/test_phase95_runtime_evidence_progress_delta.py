from __future__ import annotations

import json

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_progress_delta,
    _render_runtime_evidence_progress_delta_markdown,
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


def test_runtime_progress_delta_detects_resolved_gaps_and_improvement(tmp_path) -> None:
    previous_dir = tmp_path / "previous"
    previous_dir.mkdir()
    previous_scoreboard = {
        "execution_integrity_score": 55,
        "execution_coverage_rate": 50.0,
        "runtime_binding_success_rate": 60.0,
        "evidence_maturity": {"level": "runtime_evidence_blocked", "customer_ready": False},
    }
    previous_plan = {
        "p0_group_count": 2,
        "queued_candidate_count": 2,
        "priority_groups": [
            {"priority": "P0", "gap_type": "runtime_binding_not_fully_bound"},
            {"priority": "P0", "gap_type": "fixture_setup_not_fully_accepted"},
        ],
    }
    previous_pack = {"customer_ready_reproduction_count": 1, "packages": [{"candidate_id": "READY", "customer_ready": True}]}
    previous_ledger = {"customer_ready_probe_count": 1, "entries": [{"candidate_id": "READY", "customer_ready": True}]}
    (previous_dir / "grounded_probe_runtime_evidence_scoreboard.json").write_text(json.dumps(previous_scoreboard), encoding="utf-8")
    (previous_dir / "grounded_probe_runtime_evidence_remediation_plan.json").write_text(json.dumps(previous_plan), encoding="utf-8")
    (previous_dir / "grounded_probe_runtime_customer_reproduction_pack.json").write_text(json.dumps(previous_pack), encoding="utf-8")
    (previous_dir / "grounded_probe_runtime_evidence_probe_ledger.json").write_text(json.dumps(previous_ledger), encoding="utf-8")

    current_report = {
        "project_id": "progress-demo",
        "runtime_rerun_selection": {"enabled": True, "selected_probe_count": 1, "skipped_probe_count": 1},
        "runtime_evidence_scoreboard": {
            "execution_integrity_score": 72,
            "execution_coverage_rate": 60.0,
            "runtime_binding_success_rate": 100.0,
            "evidence_maturity": {"level": "runtime_evidence_needs_hardening", "customer_ready": False},
        },
        "runtime_evidence_remediation_plan": {
            "p0_group_count": 1,
            "queued_candidate_count": 1,
            "priority_groups": [{"priority": "P0", "gap_type": "fixture_setup_not_fully_accepted"}],
        },
        "runtime_customer_reproduction_pack": {"customer_ready_reproduction_count": 2},
        "runtime_evidence_probe_ledger": {"customer_ready_probe_count": 2},
        "runtime_evidence_carry_forward": {
            "carried_forward_candidate_ids": ["READY"],
            "carried_forward_reproduction_count": 1,
            "carried_forward_probe_ledger_count": 1,
        },
    }

    delta = _build_runtime_evidence_progress_delta({"runtime_evidence_progress_baseline_dir": str(previous_dir)}, current_report)

    assert delta["status"] == "runtime_evidence_improving"
    assert delta["metrics"]["p0_group_count"]["delta"] == -1
    assert delta["metrics"]["customer_ready_reproduction_count"]["delta"] == 1
    assert delta["resolved_gap_types"] == ["runtime_binding_not_fully_bound"]
    assert delta["persisting_gap_types"] == ["fixture_setup_not_fully_accepted"]
    assert delta["new_gap_types"] == []
    assert delta["regressions"] == []
    assert delta["carry_forward"]["candidate_count"] == 1

    markdown = _render_runtime_evidence_progress_delta_markdown(delta)
    assert "Runtime Evidence Progress Delta" in markdown
    assert "runtime_evidence_improving" in markdown
    assert "runtime_binding_not_fully_bound" in markdown


def test_targeted_rerun_writes_progress_delta_from_previous_artifacts(tmp_path) -> None:
    previous_dir = tmp_path / "previous"
    previous_dir.mkdir()
    (previous_dir / "grounded_probe_runtime_evidence_scoreboard.json").write_text(
        json.dumps(
            {
                "execution_integrity_score": 40,
                "execution_coverage_rate": 40.0,
                "runtime_binding_success_rate": 50.0,
                "evidence_maturity": {"level": "runtime_evidence_blocked", "customer_ready": False},
            }
        ),
        encoding="utf-8",
    )
    (previous_dir / "grounded_probe_runtime_evidence_remediation_plan.json").write_text(
        json.dumps(
            {
                "p0_group_count": 1,
                "queued_candidate_count": 1,
                "priority_groups": [{"priority": "P0", "gap_type": "runtime_binding_not_fully_bound"}],
                "rerun_manifest": {
                    "candidate_ids": ["GAP"],
                    "customer_ready_candidate_ids_excluded": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (previous_dir / "grounded_probe_runtime_customer_reproduction_pack.json").write_text(
        json.dumps({"customer_ready_reproduction_count": 0, "packages": []}),
        encoding="utf-8",
    )
    (previous_dir / "grounded_probe_runtime_evidence_probe_ledger.json").write_text(
        json.dumps({"customer_ready_probe_count": 0, "entries": []}),
        encoding="utf-8",
    )

    plan_path = tmp_path / "grounded_probe_plan.json"
    plan_path.write_text(json.dumps({"project_id": "progress-rerun-demo", "probes": [_probe("GAP", "/orders/gap")]}), encoding="utf-8")
    config_path = tmp_path / "probe_config.json"
    config_path.write_text(
        json.dumps({"runtime_evidence_remediation_plan_path": str(previous_dir / "grounded_probe_runtime_evidence_remediation_plan.json")}),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"

    report = run_grounded_probe_executor(probe_plan_path=plan_path, out_dir=out_dir, probe_config=config_path)

    assert "runtime_evidence_progress_delta" in report
    assert report["summary"]["runtime_progress_delta_status"] in {
        "runtime_evidence_improving",
        "runtime_evidence_unchanged",
        "runtime_evidence_regression_detected",
        "customer_ready_runtime_evidence_progress",
    }
    assert report["runtime_evidence_progress_delta"]["has_previous_evidence"] is True
    assert report["runtime_evidence_progress_delta"]["previous_sources"]["scoreboard"].endswith(
        "grounded_probe_runtime_evidence_scoreboard.json"
    )
    assert (out_dir / "grounded_probe_runtime_evidence_progress_delta.json").exists()
    assert (out_dir / "grounded_probe_runtime_evidence_progress_delta.md").exists()
