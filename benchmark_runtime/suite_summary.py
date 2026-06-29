from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


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


def _row_from_report(project: str, report_path: Path, *, status: str = "completed", error: str = "") -> dict[str, Any]:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    summary = data.get("summary") or {}
    row = {
        "project": project,
        "status": status,
        "error": error,
        "probe_count": _safe_int(summary.get("probe_count")),
        "validated_candidate_count": _safe_int(summary.get("validated_candidate_count")),
        "strong_evidence_finding_count": _safe_int(summary.get("strong_evidence_finding_count")),
        "high_finding_count": _safe_int(summary.get("high_finding_count")),
        "protected_count": _safe_int(summary.get("protected_count")),
        "needs_more_evidence_count": _safe_int(summary.get("needs_more_evidence_count")),
        "auto_snapshot_request_count": _safe_int(summary.get("auto_snapshot_request_count")),
        "executed_write_sandbox_count": _safe_int(summary.get("executed_write_sandbox_count")),
        "executed_readonly_count": _safe_int(summary.get("executed_readonly_count")),
        "commercial_handoff_status": summary.get("commercial_handoff_status"),
        "commercial_handoff_acceptance_gate_passed": bool(summary.get("commercial_handoff_acceptance_gate_passed")),
        "commercial_handoff_safe_for_customer": bool(summary.get("commercial_handoff_safe_for_customer")),
    }
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
        "commercial_handoff_status": None,
        "commercial_handoff_acceptance_gate_passed": False,
        "commercial_handoff_safe_for_customer": False,
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

    return {
        "mode": "benchmark_runtime_suite_validation",
        "run_id": manifest.get("run_id"),
        "suite_status": manifest.get("status") or ("completed" if projects else "empty"),
        "suite_started_at": manifest.get("started_at"),
        "suite_completed_at": manifest.get("completed_at"),
        "max_probes_per_project": int(max_probes_per_project),
        "project_count": len(projects),
        "completed_project_count": sum(1 for row in projects if row.get("status") == "completed"),
        "failed_project_count": sum(1 for row in projects if row.get("status") == "failed"),
        "pending_project_count": sum(1 for row in projects if row.get("status") not in {"completed", "failed"}),
        "totals": dict(totals),
        "projects": projects,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    totals = summary.get("totals") or {}
    lines = [
        "# Benchmark Runtime Suite Validation",
        "",
        f"- suite_status: {summary.get('suite_status')}",
        f"- run_id: {summary.get('run_id') or 'n/a'}",
        f"- project_count: {summary.get('project_count')}",
        f"- completed_project_count: {summary.get('completed_project_count', 0)}",
        f"- failed_project_count: {summary.get('failed_project_count', 0)}",
        f"- max_probes_per_project: {summary.get('max_probes_per_project')}",
        f"- probes: {totals.get('probe_count', 0)}",
        f"- runtime confirmed: {totals.get('validated_candidate_count', 0)}",
        f"- strong evidence: {totals.get('strong_evidence_finding_count', 0)}",
        f"- high/P1 findings: {totals.get('high_finding_count', 0)}",
        f"- protected: {totals.get('protected_count', 0)}",
        f"- needs more evidence: {totals.get('needs_more_evidence_count', 0)}",
        f"- before/after snapshot requests: {totals.get('auto_snapshot_request_count', 0)}",
        "",
        "| project | status | probes | confirmed | strong | high/P1 | snapshots | needs_more |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.get("projects") or []:
        lines.append(
            "| {project} | {status} | {probe_count} | {validated_candidate_count} | "
            "{strong_evidence_finding_count} | {high_finding_count} | "
            "{auto_snapshot_request_count} | {needs_more_evidence_count} |".format(**row)
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
        f"confirmed={totals.get('validated_candidate_count', 0)} "
        f"strong={totals.get('strong_evidence_finding_count', 0)} "
        f"high={totals.get('high_finding_count', 0)} "
        f"snapshots={totals.get('auto_snapshot_request_count', 0)} "
        f"needs_more={totals.get('needs_more_evidence_count', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
