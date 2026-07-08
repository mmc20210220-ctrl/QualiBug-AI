"""Per-scan benchmark metrics computation against seeded ground truth.

This module bridges the scan pipeline with the benchmark evaluator so that
after every scan the system computes recall, precision, FPR, FNR, evidence
completeness, reproduction success rate, and regression success rate —
but ONLY when a ground truth file exists.  Without ground truth the
benchmark section is simply absent from the result; no numbers are ever
fabricated.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "null")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        v = float(value)
        return v if v == v else fallback  # NaN guard
    except (TypeError, ValueError):
        return fallback


def _method_path_key(finding: dict[str, Any]) -> tuple[str, str]:
    """Extract a stable (method, path) key from a finding for matching."""
    method = str(finding.get("method") or finding.get("_api_method") or "").upper().strip()
    path = str(finding.get("path") or finding.get("_api_path") or "").strip().rstrip("/")
    # Normalize path params
    path = re.sub(r"/\d+", "/{id}", path)
    path = re.sub(r"/\{[^}]+\}", "/{id}", path)
    return (method, path)


def compute_benchmark(
    project: str,
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
    *,
    root: Path | None = None,
    ground_truth_path: str = "",
) -> dict[str, Any]:
    """Compute benchmark metrics for a scan run.

    Returns an empty dict when no ground truth is available — the caller
    MUST check for emptiness and never present fabricated metrics.
    """
    root = Path(root or os.environ.get("QUALIBUG_WORKSPACE_ROOT", Path.cwd()))

    # Resolve ground truth path
    gt_path: Path | None = None
    if ground_truth_path:
        gt_path = Path(ground_truth_path)
    elif os.environ.get("QUALIBUG_BENCHMARK_GROUND_TRUTH"):
        gt_path = Path(os.environ["QUALIBUG_BENCHMARK_GROUND_TRUTH"])
    else:
        # Try project-local benchmark dir first, then known absolute paths
        candidates_paths = [
            root / "platform_workspace" / project / "benchmark_ground_truth" / "bugs.json",
            root.parent / "benchmark_mall" / "hidden_ground_truth" / "bugs.json",
        ]
        # Only add absolute desktop paths when they actually exist
        _desktop = Path("C:/Users/Test/Desktop/qualibug_enterprise_benchmark_v0_5_windows_native_stable/qualibug_enterprise_benchmark_v0_5_windows_native_stable/hidden_ground_truth/bugs.json")
        if _desktop.exists():
            candidates_paths.append(_desktop)
        for p in candidates_paths:
            if p.exists():
                gt_path = p
                break

    if gt_path is None or not gt_path.exists():
        return {}  # No ground truth → no fabricated metrics

    truth_data = _read_json(gt_path)
    truth_bugs = truth_data.get("bugs", [])
    if not truth_bugs:
        return {}

    # ── Match findings against ground truth ──
    all_findings = list(findings) + (list(candidates) if candidates else [])

    # Build lookup: (method, path) → ground_truth_bug
    gt_by_path: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for bug in truth_bugs:
        trigger = bug.get("trigger", "")
        method = bug.get("method", "GET").upper()
        path = _method_path_key({"path": trigger, "method": method})[1]
        key = (method, path)
        gt_by_path.setdefault(key, []).append(bug)

    # Also build by bug_id for exact matching
    gt_by_id: dict[str, dict[str, Any]] = {}
    for bug in truth_bugs:
        bid = str(bug.get("bug_id") or bug.get("id") or "")
        if bid:
            gt_by_id[bid] = bug

    matched_gt_ids: set[str] = set()
    matched_pairs: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []

    for finding in all_findings:
        key = _method_path_key(finding)
        candidates_gt = gt_by_path.get(key, [])
        matched = False
        for gt in candidates_gt:
            gt_id = str(gt.get("bug_id") or gt.get("id") or "")
            if gt_id in matched_gt_ids:
                continue
            matched_gt_ids.add(gt_id)
            matched_pairs.append({
                "finding_title": finding.get("title", ""),
                "finding_severity": finding.get("severity", ""),
                "gt_bug_id": gt_id,
                "gt_title": gt.get("title", ""),
                "gt_severity": gt.get("severity", ""),
                "gt_type": gt.get("type", ""),
            })
            matched = True
            break
        if not matched and finding.get("customer_delivery_status") == "defect":
            false_positives.append(finding)

    total_gt = len(truth_bugs)
    total_found = len(all_findings)
    true_pos = len(matched_pairs)
    false_pos = len(false_positives)
    false_neg = max(0, total_gt - true_pos)

    # ── Sub-metrics ──
    p0p1_gt = [b for b in truth_bugs if b.get("severity") in ("P0", "P1", "critical", "high")]
    p0p1_found = [m for m in matched_pairs if m.get("gt_severity") in ("P0", "P1", "critical", "high")]

    # Evidence completeness: % of confirmed findings that have request + response + assertion
    confirmed = [f for f in findings if f.get("confirmation_status") in ("confirmed", "validated_candidate")]
    evidence_complete = 0
    for f in confirmed:
        has_req = bool(f.get("request") or (f.get("raw_evidence") or {}).get("request_raw"))
        has_resp = bool(f.get("response") or (f.get("raw_evidence") or {}).get("response_raw"))
        has_assert = bool(f.get("expected") and f.get("actual"))
        if has_req and has_resp and has_assert:
            evidence_complete += 1

    # Reproduction success rate
    repro_total = len(confirmed)
    repro_success = len([f for f in confirmed if (f.get("reproduction") or {}).get("is_synthetic") is not True and f.get("gate_passed")])

    # Regression success rate (from findings that have regression data)
    reg_total = len([f for f in findings if (f.get("regression") or {}).get("included_in_suite")])
    reg_passed = len([f for f in findings if (f.get("regression") or {}).get("latest_status") == "passed"])

    metrics = {
        "benchmark_active": True,
        "ground_truth_source": str(gt_path),
        "ground_truth_bug_count": total_gt,
        "scan_findings_total": total_found,
        "true_positives": true_pos,
        "false_positives": false_pos,
        "false_negatives": false_neg,
        "recall": round(true_pos / total_gt, 4) if total_gt else 0,
        "precision": round(true_pos / total_found, 4) if total_found else 0,
        "false_positive_rate": round(false_pos / total_found, 4) if total_found else 0,
        "false_negative_rate": round(false_neg / total_gt, 4) if total_gt else 0,
        "f1_score": round(2 * true_pos / (2 * true_pos + false_pos + false_neg), 4) if (2 * true_pos + false_pos + false_neg) > 0 else 0,
        "high_value_recall": round(len(p0p1_found) / len(p0p1_gt), 4) if p0p1_gt else 0,
        "evidence_completeness_rate": round(evidence_complete / len(confirmed), 4) if confirmed else 0,
        "evidence_complete_count": evidence_complete,
        "evidence_total_count": len(confirmed),
        "reproduction_success_rate": round(repro_success / repro_total, 4) if repro_total else 0,
        "regression_success_rate": round(reg_passed / reg_total, 4) if reg_total else 0,
        "regression_total_count": reg_total,
        "regression_passed_count": reg_passed,
        "matched_bugs": matched_pairs[:50],
        "missed_bug_ids": [b.get("bug_id") for b in truth_bugs if b.get("bug_id") not in matched_gt_ids],
        "bug_type_breakdown": _bug_type_breakdown(matched_pairs, truth_bugs),
    }
    return metrics


def _bug_type_breakdown(
    matched: list[dict[str, Any]],
    truth: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Per-bug-type recall breakdown."""
    type_map: dict[str, dict[str, int]] = {}
    for bug in truth:
        btype = str(bug.get("type") or "other").strip() or "other"
        entry = type_map.setdefault(btype, {"total": 0, "detected": 0})
        entry["total"] += 1

    gt_ids_matched = {m["gt_bug_id"] for m in matched}
    for bug in truth:
        btype = str(bug.get("type") or "other").strip() or "other"
        if bug.get("bug_id") in gt_ids_matched:
            type_map.setdefault(btype, {"total": 0, "detected": 0})["detected"] += 1

    return type_map


def persist_benchmark_result(
    project: str,
    metrics: dict[str, Any],
    *,
    root: Path | None = None,
) -> Path:
    """Persist benchmark metrics to platform_outputs so the command center can read them."""
    if not metrics:
        return Path()
    root = Path(root or Path.cwd())
    out_dir = root / "platform_outputs" / project.replace("/", "_") / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "benchmark_metrics.json"
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path
