#!/usr/bin/env python
"""Build a standard scan_result.json (the contract the external benchmark
evaluator consumes) from an input-only grounded-probe run.

This is a pure delivery adapter: it reads the grounded probe execution report
plus the document-derived candidates and emits the ``findings[]`` shape the
evaluator scores, deduplicating identical probes and enriching each finding with
the keyword-rich candidate context (title / expected behaviour / entities) so
semantic matching against ground truth is meaningful.

It never reads ground truth. Usage:

    python tools/build_scan_result_from_input_only.py <input_only_run_dir> <out_scan_result.json>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load(p: Path) -> Any:
    return json.loads(p.read_text(encoding="utf-8") or "null")


def _candidate_index(candidates: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for c in candidates:
        if isinstance(c, dict) and c.get("candidate_id"):
            index[str(c["candidate_id"])] = c
    return index


def _module_from_path(path: str) -> str:
    parts = [p for p in str(path or "").split("/") if p and not p.startswith(":") and p != "api"]
    return parts[0] if parts else ""


def _finding_record(f: dict[str, Any], cand: dict[str, Any]) -> dict[str, Any]:
    method = str(f.get("method") or "")
    path = str(f.get("path") or "")
    risk = str(f.get("risk_type") or "")
    reason = str(f.get("reason") or "")
    cand_title = str(cand.get("title") or "")
    expected = str(cand.get("expected_behavior") or "")
    entities = [str(e) for e in (cand.get("affected_entities") or []) if str(e).strip()]
    module = _module_from_path(path)
    steps = []
    probe_plan = cand.get("probe_plan") if isinstance(cand.get("probe_plan"), dict) else {}
    for s in (probe_plan.get("steps") or []):
        if str(s).strip():
            steps.append(str(s).strip())
    ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
    dbe = f.get("db_evidence") if isinstance(f.get("db_evidence"), dict) else {}
    # keyword-rich title so the evaluator's module + keyword matcher can bind
    title = cand_title or f"{risk} {method} {path}"
    return {
        "title": f"{title} [{method} {path}]",
        "severity": str(f.get("severity") or "P2"),
        "category": risk,
        "module": module,
        "expected": expected or "按接口契约应返回 4xx / 拒绝越权 / 保持业务不变量",
        "actual": reason,
        "oracle": {
            "risk_type": risk,
            "defect_class": f.get("defect_class") or risk,
            "evidence_grade": f.get("evidence_grade"),
            "violated_invariants": f.get("violated_invariants") or [],
            "affected_entities": entities,
        },
        "evidence": {
            "request": f"{method} {path}",
            "status_code": ev.get("status_code"),
            "reproduction_steps": steps or [
                f"以候选身份对 {method} {path} 发起探针请求",
                f"观测响应: {reason}",
            ],
        },
        "db_evidence": dbe if dbe else {"status": "not_captured"},
        "confidence": f.get("confidence"),
        "candidate_id": f.get("candidate_id"),
        "finding_id": f.get("finding_id"),
    }


def main() -> None:
    run_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_e2e_run/platform_outputs/benchmark_mall/input_only_run")
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else run_dir / "scan_result.json"
    report = _load(run_dir / "grounded_probe_execution_report.json") or {}
    cands = _load(run_dir / "grounded_candidates.json") or {}
    candidate_index = _candidate_index(cands.get("candidates") or [])
    raw_findings = [f for f in (report.get("findings") or []) if isinstance(f, dict)]

    # Deduplicate on (risk_type, method, path, defect_class): identical probe
    # families collapse to one reported defect, keeping the highest confidence.
    seen: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for f in raw_findings:
        key = (
            str(f.get("risk_type") or ""),
            str(f.get("method") or ""),
            str(f.get("path") or ""),
            str(f.get("defect_class") or ""),
        )
        prev = seen.get(key)
        if prev is None or float(f.get("confidence") or 0) > float(prev.get("confidence") or 0):
            seen[key] = f
    findings = [
        _finding_record(f, candidate_index.get(str(f.get("candidate_id") or ""), {}))
        for f in seen.values()
    ]

    summary = report.get("summary") or {}
    scan_result = {
        "success": True,
        "scan_id": f"scan_input_only_{report.get('project_id') or 'project'}",
        "project": report.get("project_id"),
        "grade": "runtime_validated" if findings else "no_findings",
        "score": len(findings),
        "coverage": None,
        "total_findings": len(findings),
        "raw_finding_count": len(raw_findings),
        "dedupe_report": {
            "input_count": len(raw_findings),
            "unique_count": len(findings),
            "collapsed_count": len(raw_findings) - len(findings),
        },
        "db_verification": {
            "status": "captured" if any(
                isinstance(f.get("db_evidence"), dict) and f["db_evidence"].get("status") == "captured"
                for f in findings
            ) else "not_captured",
            "findings_with_db_evidence": sum(
                1 for f in findings if isinstance(f.get("db_evidence"), dict) and f["db_evidence"].get("status") == "captured"
            ),
        },
        "discovery_verdict": summary.get("runtime_scoreboard_evidence_maturity_level"),
        "findings": findings,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(scan_result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path} : {len(findings)} unique findings from {len(raw_findings)} raw")


if __name__ == "__main__":
    main()
