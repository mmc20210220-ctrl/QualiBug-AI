from __future__ import annotations

"""Blind benchmark-suite runner.

Runs the input-only enterprise document path over a benchmark suite without
reading oracle, ground-truth, BUG_MATRIX, seed or answer files.  It only enters
``projects/<project>/input`` for each project.
"""

import json
import time
from pathlib import Path
from typing import Any

from .blind_project_runner import FORBIDDEN_TOKENS, run_input_only_project
from .real_project_onboarding import ROOT, _safe_project_id


def discover_input_projects(suite_root: str | Path) -> list[Path]:
    root = Path(suite_root).resolve()
    candidates: list[Path] = []
    if (root / "projects").is_dir():
        root = root / "projects"
    for item in sorted(root.iterdir() if root.exists() else []):
        input_dir = item / "input"
        if input_dir.is_dir():
            candidates.append(input_dir)
    return candidates


def run_blind_benchmark_suite(
    *,
    suite_root: str | Path,
    root: str | Path | None = None,
    project_prefix: str = "bench",
    limit: int | None = None,
) -> dict[str, Any]:
    root_path = Path(root or ROOT).resolve()
    inputs = discover_input_projects(suite_root)
    if limit and limit > 0:
        inputs = inputs[:limit]

    started = time.time()
    project_results: list[dict[str, Any]] = []
    for idx, input_dir in enumerate(inputs, 1):
        project_id = _safe_project_id(f"{project_prefix}_{idx:02d}_{input_dir.parent.name[:32]}")
        result = run_input_only_project(project_input_dir=input_dir, project_id=project_id, root=root_path)
        ds = result.get("discovery_summary") or {}
        project_results.append({
            "project_id": project_id,
            "project_name": input_dir.parent.name,
            "strict_no_peek": result.get("strict_no_peek"),
            "input_files": len((result.get("input_manifest") or {}).get("allowed_input_files") or []),
            "blocked_files": len((result.get("input_manifest") or {}).get("blocked_files") or []),
            "candidate_count": ds.get("issue_count"),
            "runtime_confirmed_bugs": ds.get("confirmed_runtime_bugs"),
            "risk_types": ds.get("risk_types"),
            "by_execution_policy": ds.get("by_execution_policy"),
            "outputs": result.get("outputs"),
        })

    totals = {
        "project_count": len(project_results),
        "candidate_count": sum(int(p.get("candidate_count") or 0) for p in project_results),
        "runtime_confirmed_bugs": sum(int(p.get("runtime_confirmed_bugs") or 0) for p in project_results),
        "blocked_files": sum(int(p.get("blocked_files") or 0) for p in project_results),
    }
    report = {
        "mode": "blind_benchmark_input_only",
        "strict_no_peek": True,
        "suite_root": str(Path(suite_root).resolve()),
        "forbidden_sources": sorted(FORBIDDEN_TOKENS),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "duration_seconds": round(time.time() - started, 3),
        "totals": totals,
        "projects": project_results,
        "note": "Only projects/<project>/input directories were read. Runtime confirmed bugs remain 0 unless a live/disposable target is configured and evidence gates validate findings.",
    }
    out_dir = root_path / "platform_outputs" / "blind_benchmark_suite"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "blind_benchmark_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "blind_benchmark_summary.md").write_text(render_blind_benchmark_summary(report), encoding="utf-8")
    report["outputs"] = {
        "summary_json": str(out_dir / "blind_benchmark_summary.json"),
        "summary_md": str(out_dir / "blind_benchmark_summary.md"),
    }
    return report


def render_blind_benchmark_summary(report: dict[str, Any]) -> str:
    totals = report.get("totals") or {}
    rows = []
    for p in report.get("projects") or []:
        rows.append(
            f"| {p.get('project_name')} | {p.get('candidate_count')} | {p.get('runtime_confirmed_bugs')} | "
            f"{', '.join(p.get('risk_types') or [])} |"
        )
    return "\n".join([
        "# Blind Benchmark Input-only Summary",
        "",
        f"- strict_no_peek: `{report.get('strict_no_peek')}`",
        f"- suite_root: `{report.get('suite_root')}`",
        f"- projects: {totals.get('project_count')}",
        f"- document-derived candidates: {totals.get('candidate_count')}",
        f"- runtime confirmed bugs: {totals.get('runtime_confirmed_bugs')}",
        f"- blocked files: {totals.get('blocked_files')}",
        "",
        "| Project | Candidates | Runtime confirmed | Risk types |",
        "|---|---:|---:|---|",
        *rows,
        "",
        "> This summary is blind: oracle/ground_truth/BUG_MATRIX/seed/answer files are not read.",
    ])
