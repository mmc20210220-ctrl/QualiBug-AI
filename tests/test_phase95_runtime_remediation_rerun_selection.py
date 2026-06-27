from __future__ import annotations

import json

from ai_test_asset_center.grounded_probe_executor import (
    _apply_runtime_rerun_selection,
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


def test_runtime_remediation_rerun_manifest_selects_only_queued_candidates() -> None:
    probes = [_probe("READY"), _probe("BINDING-GAP"), _probe("FIXTURE-GAP")]
    config = {
        "runtime_evidence_remediation_plan": {
            "rerun_manifest": {
                "candidate_ids": ["BINDING-GAP", "MISSING-CANDIDATE", "BINDING-GAP"],
                "customer_ready_candidate_ids_excluded": ["READY"],
            }
        }
    }

    selected, receipt = _apply_runtime_rerun_selection(probes, config)

    assert [p["candidate_id"] for p in selected] == ["BINDING-GAP"]
    assert receipt["enabled"] is True
    assert receipt["status"] == "targeted_runtime_rerun_selection_applied"
    assert receipt["requested_candidate_count"] == 2
    assert receipt["selected_probe_count"] == 1
    assert receipt["skipped_probe_count"] == 2
    assert receipt["missing_candidate_ids"] == ["MISSING-CANDIDATE"]
    assert receipt["customer_ready_candidate_ids_excluded"] == ["READY"]


def test_grounded_executor_applies_runtime_rerun_selection_to_report(tmp_path) -> None:
    plan_path = tmp_path / "grounded_probe_plan.json"
    out_dir = tmp_path / "out"
    plan = {
        "project_id": "rerun-selection-demo",
        "probes": [
            _probe("READY", "/orders/ready"),
            _probe("BINDING-GAP", "/orders/gap"),
            _probe("FIXTURE-GAP", "/orders/fixture-gap"),
        ],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    config_path = tmp_path / "probe_config.json"
    config_path.write_text(
        json.dumps(
            {
                "runtime_evidence_rerun_manifest": {
                    "candidate_ids": ["BINDING-GAP"],
                    "customer_ready_candidate_ids_excluded": ["READY"],
                }
            }
        ),
        encoding="utf-8",
    )

    report = run_grounded_probe_executor(
        probe_plan_path=plan_path,
        out_dir=out_dir,
        probe_config=config_path,
    )

    assert report["summary"]["probe_count"] == 1
    assert report["summary"]["runtime_rerun_selection_enabled"] is True
    assert report["summary"]["runtime_rerun_skipped_probe_count"] == 2
    assert report["runtime_rerun_selection"]["candidate_ids"] == ["BINDING-GAP"]
    assert [decision["candidate_id"] for decision in report["decisions"]] == ["BINDING-GAP"]
    assert (out_dir / "grounded_probe_runtime_evidence_scoreboard.json").exists()
