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
