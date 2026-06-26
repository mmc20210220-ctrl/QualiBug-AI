from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def build_summary(suite_out: str | Path, *, max_probes_per_project: int) -> dict[str, Any]:
    root = Path(suite_out).resolve()
    reports = sorted(root.glob("*/grounded_probe_execution_report.json"))
    projects: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()

    for path in reports:
        data = json.loads(path.read_text(encoding="utf-8"))
        summary = data.get("summary") or {}
        row = {
            "project": path.parent.name,
            "probe_count": int(summary.get("probe_count") or 0),
            "validated_candidate_count": int(summary.get("validated_candidate_count") or 0),
            "strong_evidence_finding_count": int(summary.get("strong_evidence_finding_count") or 0),
            "high_finding_count": int(summary.get("high_finding_count") or 0),
            "protected_count": int(summary.get("protected_count") or 0),
            "needs_more_evidence_count": int(summary.get("needs_more_evidence_count") or 0),
            "auto_snapshot_request_count": int(summary.get("auto_snapshot_request_count") or 0),
            "executed_write_sandbox_count": int(summary.get("executed_write_sandbox_count") or 0),
            "executed_readonly_count": int(summary.get("executed_readonly_count") or 0),
            "commercial_handoff_status": summary.get("commercial_handoff_status"),
            "commercial_handoff_acceptance_gate_passed": bool(summary.get("commercial_handoff_acceptance_gate_passed")),
            "commercial_handoff_safe_for_customer": bool(summary.get("commercial_handoff_safe_for_customer")),
        }
        projects.append(row)
        for key, value in row.items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] += value

    return {
        "mode": "benchmark_runtime_suite_validation",
        "max_probes_per_project": int(max_probes_per_project),
        "project_count": len(projects),
        "totals": dict(totals),
        "projects": projects,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    totals = summary.get("totals") or {}
    lines = [
        "# Benchmark Runtime Suite Validation",
        "",
        f"- project_count: {summary.get('project_count')}",
        f"- max_probes_per_project: {summary.get('max_probes_per_project')}",
        f"- probes: {totals.get('probe_count', 0)}",
        f"- runtime confirmed: {totals.get('validated_candidate_count', 0)}",
        f"- strong evidence: {totals.get('strong_evidence_finding_count', 0)}",
        f"- high/P1 findings: {totals.get('high_finding_count', 0)}",
        f"- protected: {totals.get('protected_count', 0)}",
        f"- needs more evidence: {totals.get('needs_more_evidence_count', 0)}",
        f"- before/after snapshot requests: {totals.get('auto_snapshot_request_count', 0)}",
        "",
        "| project | probes | confirmed | strong | high/P1 | snapshots | needs_more |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in summary.get("projects") or []:
        lines.append(
            "| {project} | {probe_count} | {validated_candidate_count} | "
            "{strong_evidence_finding_count} | {high_finding_count} | "
            "{auto_snapshot_request_count} | {needs_more_evidence_count} |".format(**row)
        )
    return "\n".join(lines) + "\n"


def write_summary(suite_out: str | Path, *, max_probes_per_project: int) -> dict[str, Any]:
    root = Path(suite_out).resolve()
    root.mkdir(parents=True, exist_ok=True)
    summary = build_summary(root, max_probes_per_project=max_probes_per_project)
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
    args = parser.parse_args()

    summary = write_summary(args.suite_out, max_probes_per_project=args.max_probes_per_project)
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
