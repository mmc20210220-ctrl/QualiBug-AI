from __future__ import annotations

import json
from pathlib import Path

from benchmark_runtime.suite_summary import build_summary


def _write_report(path: Path, *, probe_count: int, validated: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "probe_count": probe_count,
            "validated_candidate_count": validated,
            "strong_evidence_finding_count": validated,
            "high_finding_count": validated,
            "protected_count": 0,
            "needs_more_evidence_count": 0,
            "auto_snapshot_request_count": 0,
            "executed_write_sandbox_count": 0,
            "executed_readonly_count": probe_count,
            "commercial_handoff_status": "draft",
            "commercial_handoff_acceptance_gate_passed": False,
            "commercial_handoff_safe_for_customer": False,
        }
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_discovery_payload(
    path: Path,
    *,
    probe_count: int,
    candidate: int,
    pending: int,
    validated: int,
    verifier_passed: int,
    repro_ready: int,
    evidence_ready: int,
    discovery_funnel: dict | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": {
            "probe_count": probe_count,
            "candidate_issue_count": candidate,
            "pending_finding_count": pending,
            "validated_bug_count": validated,
            "verifier_passed_issue_count": verifier_passed,
            "reproduction_ready_issue_count": repro_ready,
            "evidence_ref_ready_issue_count": evidence_ready,
            "reporting_basis": "validated_bug",
            "validated_bug_discovery_rate": round(validated / max(1, probe_count), 3),
            "repro_success_rate": round(repro_ready / max(1, verifier_passed), 3),
            "evidence_complete_rate": round(evidence_ready / max(1, verifier_passed), 3),
            "low_discovery_diagnosis": {
                "primary_category": "evidence_insufficient",
                "validated_bug_discovery_rate": round(validated / max(1, probe_count), 3),
            },
        },
        "discovery_funnel": discovery_funnel or {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_suite_summary_prefers_manifest_and_marks_failed_projects_without_reports(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "20260629_000001"
    project_a = run_root / "01_demo"
    project_b = run_root / "02_demo"
    _write_report(project_a / "grounded_probe_execution_report.json", probe_count=20, validated=18)
    manifest = {
        "run_id": "20260629_000001",
        "status": "partial_failed",
        "started_at": "2026-06-29T00:00:00Z",
        "completed_at": "2026-06-29T00:05:00Z",
        "projects": [
            {"project": "01_demo", "status": "completed", "out_dir": str(project_a), "error": ""},
            {"project": "02_demo", "status": "failed", "out_dir": str(project_b), "error": "executor crashed"},
        ],
    }
    manifest_path = run_root / "suite_runtime_run_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    summary = build_summary(run_root, max_probes_per_project=20, manifest_path=manifest_path)

    assert summary["suite_status"] == "partial_failed"
    assert summary["run_id"] == "20260629_000001"
    assert summary["project_count"] == 2
    assert summary["completed_project_count"] == 1
    assert summary["failed_project_count"] == 1
    assert summary["totals"]["probe_count"] == 20
    failed = next(row for row in summary["projects"] if row["project"] == "02_demo")
    assert failed["status"] == "failed"
    assert failed["error"] == "executor crashed"
    assert failed["probe_count"] == 0
    assert failed["validated_bug_count"] == 0
    assert failed["reporting_basis"] == "validated_bug"


def test_suite_summary_uses_validated_bug_reporting_basis_from_discovery_payload(tmp_path: Path) -> None:
    run_root = tmp_path / "runs" / "20260629_000002"
    project_a = run_root / "01_demo"
    _write_report(project_a / "grounded_probe_execution_report.json", probe_count=20, validated=18)
    _write_discovery_payload(
        project_a / "real_project" / "real_project_defect_data.json",
        probe_count=20,
        candidate=5,
        pending=4,
        validated=9,
        verifier_passed=12,
        repro_ready=10,
        evidence_ready=9,
        discovery_funnel={
            "candidate_generation": {"input_count": 20, "output_count": 20, "drop_count": 0, "conversion_rate": 1.0},
            "probe_selection": {"input_count": 20, "output_count": 16, "drop_count": 4, "conversion_rate": 0.8},
            "execution": {"input_count": 16, "output_count": 14, "drop_count": 2, "conversion_rate": 0.875},
            "verification": {"input_count": 12, "output_count": 12, "drop_count": 0, "conversion_rate": 1.0},
            "formal_accounting": {"input_count": 12, "output_count": 9, "drop_count": 3, "conversion_rate": 0.75},
        },
    )

    summary = build_summary(run_root, max_probes_per_project=20)

    assert summary["reporting_basis"] == "validated_bug"
    assert summary["totals"]["validated_bug_count"] == 9
    assert summary["totals"]["validated_bug_discovery_rate"] == 0.45
    assert summary["totals"]["repro_success_rate"] == 0.833
    assert summary["totals"]["evidence_complete_rate"] == 0.75
    assert summary["representative_benchmark_project"] == "01_demo"
    assert summary["representative_benchmark_funnel_focus_stage"] == "formal_accounting"
    comparison = summary["representative_benchmark_funnel_before_after"]
    assert comparison["legacy_reporting_basis"] == "validated_candidate"
    assert comparison["strict_reporting_basis"] == "validated_bug"
    assert comparison["focus_stage"] == "formal_accounting"
    assert comparison["before"]["formal_accounting"]["output_count"] == 12
    assert comparison["after"]["formal_accounting"]["output_count"] == 9
    assert comparison["formal_accounting_delta"]["output_count_delta"] == -3
    assert comparison["exposes_remaining_bottleneck"] is True

    row = summary["projects"][0]
    assert row["validated_candidate_count"] == 18
    assert row["candidate_issue_count"] == 5
    assert row["pending_finding_count"] == 4
    assert row["validated_bug_count"] == 9
    assert row["validated_bug_discovery_rate"] == 0.45
    assert row["repro_success_rate"] == 0.833
    assert row["evidence_complete_rate"] == 0.75
    assert row["low_discovery_diagnosis"]["primary_category"] == "evidence_insufficient"
    assert row["benchmark_funnel_before_after"]["focus_stage"] == "formal_accounting"
