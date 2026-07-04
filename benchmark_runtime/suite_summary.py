from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


_FUNNEL_STAGE_ORDER = (
    "candidate_generation",
    "probe_selection",
    "execution",
    "verification",
    "formal_accounting",
)


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def _load_manifest(manifest_path: str | Path | None) -> dict[str, Any]:
    if not manifest_path:
        return {}
    path = Path(manifest_path).resolve()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 3)


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_discovery_payload(report_dir: Path) -> dict[str, Any]:
    for candidate in (
        report_dir / "real_project_defect_data.json",
        report_dir / "real_project" / "real_project_defect_data.json",
    ):
        payload = _load_json(candidate)
        if payload:
            return payload
    return {}


def _normalize_funnel_stage(stage_name: str, stage_payload: Any) -> dict[str, Any]:
    stage = stage_payload if isinstance(stage_payload, dict) else {}
    input_count = _safe_int(stage.get("input_count"))
    output_count = _safe_int(stage.get("output_count"))
    return {
        "stage": stage_name,
        "input_count": input_count,
        "output_count": output_count,
        "drop_count": _safe_int(stage.get("drop_count") or max(0, input_count - output_count)),
        "conversion_rate": float(stage.get("conversion_rate") or 0.0),
        "top_blockers": list(stage.get("top_blockers") or []),
    }


def _build_benchmark_funnel_before_after(
    project: str,
    summary: dict[str, Any],
    discovery_payload: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    discovery_funnel = discovery_payload.get("discovery_funnel") if isinstance(discovery_payload.get("discovery_funnel"), dict) else {}
    if not discovery_funnel:
        return {}

    after = {
        stage_name: _normalize_funnel_stage(stage_name, discovery_funnel.get(stage_name))
        for stage_name in _FUNNEL_STAGE_ORDER
    }
    before = {stage_name: dict(stage_payload) for stage_name, stage_payload in after.items()}
    legacy_validated_count = max(
        _safe_int(summary.get("validated_candidate_count")),
        _safe_int(summary.get("strong_evidence_finding_count")),
        _safe_int(summary.get("high_finding_count")),
    )
    verifier_passed_issue_count = max(
        _safe_int(row.get("verifier_passed_issue_count")),
        _safe_int(after.get("verification", {}).get("output_count")),
        _safe_int(after.get("formal_accounting", {}).get("input_count")),
    )
    legacy_validated_count = min(legacy_validated_count, verifier_passed_issue_count) if verifier_passed_issue_count else legacy_validated_count
    before["formal_accounting"] = {
        "stage": "formal_accounting",
        "input_count": verifier_passed_issue_count,
        "output_count": legacy_validated_count,
        "drop_count": max(0, verifier_passed_issue_count - legacy_validated_count),
        "conversion_rate": _safe_rate(legacy_validated_count, verifier_passed_issue_count),
        "top_blockers": [],
    }

    stage_deltas: list[dict[str, Any]] = []
    for stage_name in _FUNNEL_STAGE_ORDER:
        before_stage = before[stage_name]
        after_stage = after[stage_name]
        stage_deltas.append(
            {
                "stage": stage_name,
                "before_output_count": _safe_int(before_stage.get("output_count")),
                "after_output_count": _safe_int(after_stage.get("output_count")),
                "output_count_delta": _safe_int(after_stage.get("output_count")) - _safe_int(before_stage.get("output_count")),
                "before_conversion_rate": round(float(before_stage.get("conversion_rate") or 0.0), 3),
                "after_conversion_rate": round(float(after_stage.get("conversion_rate") or 0.0), 3),
                "conversion_rate_delta": round(
                    float(after_stage.get("conversion_rate") or 0.0) - float(before_stage.get("conversion_rate") or 0.0),
                    3,
                ),
            }
        )

    focus_row = min(
        stage_deltas,
        key=lambda item: (item.get("after_conversion_rate", 0.0), item.get("output_count_delta", 0)),
    )
    formal_delta = next(
        (item for item in stage_deltas if item.get("stage") == "formal_accounting"),
        {"output_count_delta": 0, "conversion_rate_delta": 0.0},
    )
    return {
        "project": project,
        "legacy_reporting_basis": "validated_candidate",
        "strict_reporting_basis": "validated_bug",
        "before": before,
        "after": after,
        "stage_deltas": stage_deltas,
        "focus_stage": str(focus_row.get("stage") or ""),
        "formal_accounting_delta": formal_delta,
        "exposes_remaining_bottleneck": bool(formal_delta.get("output_count_delta", 0) < 0),
    }


def _pick_representative_benchmark(projects: list[dict[str, Any]]) -> dict[str, Any]:
    comparable = [row for row in projects if isinstance(row.get("benchmark_funnel_before_after"), dict) and row.get("benchmark_funnel_before_after")]
    if not comparable:
        return {}
    return max(
        comparable,
        key=lambda row: (
            abs(_safe_int(((row.get("benchmark_funnel_before_after") or {}).get("formal_accounting_delta") or {}).get("output_count_delta"))),
            _safe_int(row.get("probe_count")),
            _safe_int(row.get("validated_candidate_count")),
        ),
    )


def _row_from_report(project: str, report_path: Path, *, status: str = "completed", error: str = "") -> dict[str, Any]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    summary = data.get("summary") or {}
    discovery_payload = _load_discovery_payload(report_path.parent)
    discovery_summary = discovery_payload.get("summary") if isinstance(discovery_payload.get("summary"), dict) else {}
    discovery_metrics = discovery_payload.get("metrics") if isinstance(discovery_payload.get("metrics"), dict) else {}
    low_discovery_diagnosis = (
        discovery_summary.get("low_discovery_diagnosis")
        if isinstance(discovery_summary.get("low_discovery_diagnosis"), dict)
        else (discovery_payload.get("discovery_blocker_summary") or {}).get("low_discovery_diagnosis")
        if isinstance((discovery_payload.get("discovery_blocker_summary") or {}).get("low_discovery_diagnosis"), dict)
        else {}
    )
    validated_bug_count = _safe_int(discovery_summary.get("validated_bug_count") or discovery_metrics.get("validated_bug_count"))
    candidate_issue_count = _safe_int(discovery_summary.get("candidate_issue_count") or discovery_metrics.get("candidate_issue_count"))
    pending_finding_count = _safe_int(discovery_summary.get("pending_finding_count") or discovery_metrics.get("pending_finding_count"))
    verifier_passed_issue_count = _safe_int(
        discovery_summary.get("verifier_passed_issue_count") or discovery_metrics.get("verifier_passed_issue_count")
    )
    reproduction_ready_issue_count = _safe_int(
        discovery_summary.get("reproduction_ready_issue_count") or discovery_metrics.get("reproduction_ready_issue_count")
    )
    evidence_ref_ready_issue_count = _safe_int(
        discovery_summary.get("evidence_ref_ready_issue_count") or discovery_metrics.get("evidence_ref_ready_issue_count")
    )
    probe_count = _safe_int(summary.get("probe_count"))
    row = {
        "project": project,
        "status": status,
        "error": error,
        "probe_count": probe_count,
        "validated_candidate_count": _safe_int(summary.get("validated_candidate_count")),
        "strong_evidence_finding_count": _safe_int(summary.get("strong_evidence_finding_count")),
        "high_finding_count": _safe_int(summary.get("high_finding_count")),
        "protected_count": _safe_int(summary.get("protected_count")),
        "needs_more_evidence_count": _safe_int(summary.get("needs_more_evidence_count")),
        "auto_snapshot_request_count": _safe_int(summary.get("auto_snapshot_request_count")),
        "executed_write_sandbox_count": _safe_int(summary.get("executed_write_sandbox_count")),
        "executed_readonly_count": _safe_int(summary.get("executed_readonly_count")),
        "reporting_basis": str(discovery_summary.get("reporting_basis") or discovery_metrics.get("reporting_basis") or "validated_bug"),
        "candidate_issue_count": candidate_issue_count,
        "pending_finding_count": pending_finding_count,
        "validated_bug_count": validated_bug_count,
        "verifier_passed_issue_count": verifier_passed_issue_count,
        "reproduction_ready_issue_count": reproduction_ready_issue_count,
        "evidence_ref_ready_issue_count": evidence_ref_ready_issue_count,
        "validated_bug_discovery_rate": _safe_rate(validated_bug_count, probe_count),
        "repro_success_rate": _safe_rate(reproduction_ready_issue_count, verifier_passed_issue_count),
        "evidence_complete_rate": _safe_rate(evidence_ref_ready_issue_count, verifier_passed_issue_count),
        "low_discovery_diagnosis": low_discovery_diagnosis,
        "commercial_handoff_status": summary.get("commercial_handoff_status"),
        "commercial_handoff_acceptance_gate_passed": bool(summary.get("commercial_handoff_acceptance_gate_passed")),
        "commercial_handoff_safe_for_customer": bool(summary.get("commercial_handoff_safe_for_customer")),
    }
    row["benchmark_funnel_before_after"] = _build_benchmark_funnel_before_after(project, summary, discovery_payload, row)
    return row


def _row_without_report(project: str, *, status: str, error: str = "") -> dict[str, Any]:
    return {
        "project": project,
        "status": status,
        "error": error,
        "probe_count": 0,
        "validated_candidate_count": 0,
        "strong_evidence_finding_count": 0,
        "high_finding_count": 0,
        "protected_count": 0,
        "needs_more_evidence_count": 0,
        "auto_snapshot_request_count": 0,
        "executed_write_sandbox_count": 0,
        "executed_readonly_count": 0,
        "reporting_basis": "validated_bug",
        "candidate_issue_count": 0,
        "pending_finding_count": 0,
        "validated_bug_count": 0,
        "verifier_passed_issue_count": 0,
        "reproduction_ready_issue_count": 0,
        "evidence_ref_ready_issue_count": 0,
        "validated_bug_discovery_rate": 0.0,
        "repro_success_rate": 0.0,
        "evidence_complete_rate": 0.0,
        "low_discovery_diagnosis": {},
        "commercial_handoff_status": None,
        "commercial_handoff_acceptance_gate_passed": False,
        "commercial_handoff_safe_for_customer": False,
        "benchmark_funnel_before_after": {},
    }


def build_summary(
    suite_out: str | Path,
    *,
    max_probes_per_project: int,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(suite_out).resolve()
    projects: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    manifest = _load_manifest(manifest_path or (root / "suite_runtime_run_manifest.json"))
    manifest_projects = manifest.get("projects") if isinstance(manifest.get("projects"), list) else []

    if manifest_projects:
        for entry in manifest_projects:
            if not isinstance(entry, dict):
                continue
            project = str(entry.get("project") or entry.get("name") or "")
            if not project:
                continue
            out_dir_raw = str(entry.get("out_dir") or "")
            out_dir = Path(out_dir_raw).resolve() if out_dir_raw else root / project
            report_path = out_dir / "grounded_probe_execution_report.json"
            status = str(entry.get("status") or ("completed" if report_path.exists() else "pending"))
            error = str(entry.get("error") or "")
            row = _row_from_report(project, report_path, status=status, error=error) if report_path.exists() else _row_without_report(project, status=status, error=error)
            projects.append(row)
            for key, value in row.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] += value
    else:
        reports = sorted(root.glob("*/grounded_probe_execution_report.json"))
        for path in reports:
            row = _row_from_report(path.parent.name, path)
            projects.append(row)
            for key, value in row.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[key] += value

    totals_dict = dict(totals)
    totals_dict["validated_bug_discovery_rate"] = _safe_rate(
        _safe_int(totals_dict.get("validated_bug_count")),
        _safe_int(totals_dict.get("probe_count")),
    )
    totals_dict["repro_success_rate"] = _safe_rate(
        _safe_int(totals_dict.get("reproduction_ready_issue_count")),
        _safe_int(totals_dict.get("verifier_passed_issue_count")),
    )
    totals_dict["evidence_complete_rate"] = _safe_rate(
        _safe_int(totals_dict.get("evidence_ref_ready_issue_count")),
        _safe_int(totals_dict.get("verifier_passed_issue_count")),
    )
    representative_benchmark = _pick_representative_benchmark(projects)
    representative_benchmark_comparison = representative_benchmark.get("benchmark_funnel_before_after") if representative_benchmark else {}

    return {
        "mode": "benchmark_runtime_suite_validation",
        "run_id": manifest.get("run_id"),
        "suite_status": manifest.get("status") or ("completed" if projects else "empty"),
        "suite_started_at": manifest.get("started_at"),
        "suite_completed_at": manifest.get("completed_at"),
        "reporting_basis": "validated_bug",
        "max_probes_per_project": int(max_probes_per_project),
        "project_count": len(projects),
        "completed_project_count": sum(1 for row in projects if row.get("status") == "completed"),
        "failed_project_count": sum(1 for row in projects if row.get("status") == "failed"),
        "pending_project_count": sum(1 for row in projects if row.get("status") not in {"completed", "failed"}),
        "representative_benchmark_project": representative_benchmark.get("project") if representative_benchmark else None,
        "representative_benchmark_funnel_focus_stage": representative_benchmark_comparison.get("focus_stage") if representative_benchmark_comparison else None,
        "representative_benchmark_funnel_before_after": representative_benchmark_comparison,
        "totals": totals_dict,
        "projects": projects,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    totals = summary.get("totals") or {}
    lines = [
        "# Benchmark Runtime Suite Validation",
        "",
        f"- reporting_basis: {summary.get('reporting_basis')}",
        f"- suite_status: {summary.get('suite_status')}",
        f"- run_id: {summary.get('run_id') or 'n/a'}",
        f"- project_count: {summary.get('project_count')}",
        f"- completed_project_count: {summary.get('completed_project_count', 0)}",
        f"- failed_project_count: {summary.get('failed_project_count', 0)}",
        f"- max_probes_per_project: {summary.get('max_probes_per_project')}",
        f"- representative benchmark: {summary.get('representative_benchmark_project') or 'n/a'}",
        f"- representative funnel focus stage: {summary.get('representative_benchmark_funnel_focus_stage') or 'n/a'}",
        f"- probes: {totals.get('probe_count', 0)}",
        f"- candidate findings: {totals.get('candidate_issue_count', 0)}",
        f"- pending findings: {totals.get('pending_finding_count', 0)}",
        f"- validated bugs: {totals.get('validated_bug_count', 0)}",
        f"- discovery rate: {totals.get('validated_bug_discovery_rate', 0.0)}",
        f"- repro success rate: {totals.get('repro_success_rate', 0.0)}",
        f"- evidence complete rate: {totals.get('evidence_complete_rate', 0.0)}",
        f"- legacy runtime confirmed: {totals.get('validated_candidate_count', 0)}",
        f"- strong evidence: {totals.get('strong_evidence_finding_count', 0)}",
        f"- high/P1 findings: {totals.get('high_finding_count', 0)}",
        f"- protected: {totals.get('protected_count', 0)}",
        f"- needs more evidence: {totals.get('needs_more_evidence_count', 0)}",
        f"- before/after snapshot requests: {totals.get('auto_snapshot_request_count', 0)}",
        "",
        "| project | status | probes | candidate | pending | validated | discovery | repro | evidence |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.get("projects") or []:
        lines.append(
            "| {project} | {status} | {probe_count} | {candidate_issue_count} | "
            "{pending_finding_count} | {validated_bug_count} | {validated_bug_discovery_rate} | "
            "{repro_success_rate} | {evidence_complete_rate} |".format(**row)
        )
    return "\n".join(lines) + "\n"


def write_summary(
    suite_out: str | Path,
    *,
    max_probes_per_project: int,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(suite_out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    summary = build_summary(root, max_probes_per_project=max_probes_per_project, manifest_path=manifest_path)
    (root / "suite_runtime_validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (root / "suite_runtime_validation_summary.md").write_text(render_markdown(summary), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggregate QualiBug benchmark runtime execution reports.")
    parser.add_argument("--suite-out", required=True)
    parser.add_argument("--max-probes-per-project", type=int, required=True)
    parser.add_argument("--manifest")
    args = parser.parse_args()

    summary = write_summary(args.suite_out, max_probes_per_project=args.max_probes_per_project, manifest_path=args.manifest)
    totals = summary.get("totals") or {}
    print(
        "SUITE_RUNTIME_VALIDATION_SUMMARY "
        f"projects={summary.get('project_count')} "
        f"probes={totals.get('probe_count', 0)} "
        f"validated={totals.get('validated_bug_count', 0)} "
        f"discovery_rate={totals.get('validated_bug_discovery_rate', 0.0)} "
        f"repro_rate={totals.get('repro_success_rate', 0.0)} "
        f"evidence_rate={totals.get('evidence_complete_rate', 0.0)} "
        f"strong={totals.get('strong_evidence_finding_count', 0)} "
        f"high={totals.get('high_finding_count', 0)} "
        f"snapshots={totals.get('auto_snapshot_request_count', 0)} "
        f"needs_more={totals.get('needs_more_evidence_count', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
