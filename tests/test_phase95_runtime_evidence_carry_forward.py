from __future__ import annotations

import json

from ai_test_asset_center.grounded_probe_executor import (
    _build_runtime_evidence_carry_forward,
    _build_runtime_customer_reproduction_pack,
    _build_runtime_evidence_probe_ledger,
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


def _write_previous_ready_artifacts(previous_dir) -> None:
    (previous_dir / "grounded_probe_runtime_customer_reproduction_pack.json").write_text(
        json.dumps(
            {
                "engine": "runtime_customer_reproduction_pack_v1_phase95",
                "packages": [
                    {
                        "finding_id": "GPF-READY",
                        "candidate_id": "READY",
                        "title": "ready validated finding",
                        "customer_ready": True,
                        "readiness_level": "customer_ready_candidate",
                        "reproduction_readiness_gate": {"customer_ready": True, "blockers": [], "level": "customer_ready_candidate"},
                        "reproduction_trace": [
                            {"sequence": 1, "phase": "target", "method": "GET", "path": "/orders/ready", "status_code": 200}
                        ],
                    },
                    {
                        "finding_id": "GPF-BLOCKED",
                        "candidate_id": "BLOCKED-READY",
                        "customer_ready": False,
                        "readiness_level": "validated_but_reproduction_gap",
                        "reproduction_readiness_gate": {"customer_ready": False, "blockers": ["missing_target_reproduction_step"]},
                        "reproduction_trace": [],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (previous_dir / "grounded_probe_runtime_evidence_probe_ledger.json").write_text(
        json.dumps(
            {
                "engine": "runtime_evidence_probe_ledger_v1_phase95",
                "entries": [
                    {
                        "candidate_id": "READY",
                        "customer_ready": True,
                        "readiness_level": "customer_ready_candidate",
                        "verdict": "validated_candidate",
                        "gap_types": [],
                    },
                    {
                        "candidate_id": "BLOCKED-READY",
                        "customer_ready": False,
                        "readiness_level": "evidence_gap",
                        "gap_types": ["missing_target_reproduction_step"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_runtime_evidence_carry_forward_loads_only_customer_ready_previous_evidence(tmp_path) -> None:
    previous_dir = tmp_path / "previous"
    previous_dir.mkdir()
    _write_previous_ready_artifacts(previous_dir)
    plan_path = previous_dir / "grounded_probe_runtime_evidence_remediation_plan.json"
    plan_path.write_text(
        json.dumps(
            {
                "rerun_manifest": {
                    "candidate_ids": ["GAP"],
                    "customer_ready_candidate_ids_excluded": ["READY", "BLOCKED-READY"],
                }
            }
        ),
        encoding="utf-8",
    )

    carry = _build_runtime_evidence_carry_forward(
        {"runtime_evidence_remediation_plan_path": str(plan_path)},
        {
            "enabled": True,
            "sources": [f"runtime_evidence_remediation_plan_path:{plan_path}"],
            "customer_ready_candidate_ids_excluded": ["READY", "BLOCKED-READY"],
        },
    )

    assert carry["status"] == "customer_ready_evidence_carried_forward"
    assert carry["carried_forward_candidate_ids"] == ["READY"]
    assert carry["carried_forward_reproduction_count"] == 1
    assert carry["carried_forward_probe_ledger_count"] == 1
    assert carry["packages"][0]["carried_forward"] is True
    assert carry["blocked_candidate_count"] >= 1


def test_targeted_rerun_preserves_skipped_customer_ready_reproduction_pack(tmp_path) -> None:
    previous_dir = tmp_path / "previous"
    previous_dir.mkdir()
    _write_previous_ready_artifacts(previous_dir)
    remediation_plan_path = previous_dir / "grounded_probe_runtime_evidence_remediation_plan.json"
    remediation_plan_path.write_text(
        json.dumps(
            {
                "rerun_manifest": {
                    "candidate_ids": ["GAP"],
                    "customer_ready_candidate_ids_excluded": ["READY"],
                }
            }
        ),
        encoding="utf-8",
    )

    plan_path = tmp_path / "grounded_probe_plan.json"
    out_dir = tmp_path / "out"
    plan_path.write_text(
        json.dumps({"project_id": "carry-forward-demo", "probes": [_probe("READY", "/orders/ready"), _probe("GAP", "/orders/gap")]}),
        encoding="utf-8",
    )
    config_path = tmp_path / "probe_config.json"
    config_path.write_text(json.dumps({"runtime_evidence_remediation_plan_path": str(remediation_plan_path)}), encoding="utf-8")

    report = run_grounded_probe_executor(probe_plan_path=plan_path, out_dir=out_dir, probe_config=config_path)

    assert [decision["candidate_id"] for decision in report["decisions"]] == ["GAP"]
    assert report["runtime_evidence_carry_forward"]["carried_forward_candidate_ids"] == ["READY"]
    assert report["summary"]["runtime_carry_forward_reproduction_count"] == 1
    assert report["runtime_evidence_probe_ledger"]["carried_forward_probe_count"] == 1
    assert report["runtime_customer_reproduction_pack"]["carried_forward_reproduction_count"] == 1
    assert report["runtime_customer_reproduction_pack"]["customer_ready_reproduction_count"] == 1
    assert report["runtime_customer_reproduction_pack"]["packages"][0]["candidate_id"] == "READY"
    assert (out_dir / "grounded_probe_runtime_evidence_carry_forward.json").exists()
    assert (out_dir / "grounded_probe_runtime_evidence_carry_forward.md").exists()
