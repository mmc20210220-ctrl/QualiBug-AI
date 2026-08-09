#!/usr/bin/env python
"""External benchmark evaluator for QualiBug scan output.

Reads the scan_result.json produced by QualiBug and the benchmark's hidden
ground truth, then reports coverage / reproduction / evidence-completeness.

This is an EXTERNAL evaluator. QualiBug's own scan never reads ground truth;
this script does, purely to score results after the fact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def _load(p: Path):
    # scan_result 分片 store 自动组装（findings 为小分片，按需加载）；GT 等
    # 普通 JSON 行为与 json.loads 一致。
    if p.name == "scan_result.json":
        from ai_test_asset_center.scan_result_store import load_scan_result

        return load_scan_result(p, keys=["findings"])
    return json.loads(p.read_text(encoding="utf-8"))


def _text_blob(f: dict) -> str:
    parts = [
        str(f.get("title") or ""),
        str(f.get("expected") or ""),
        str(f.get("actual") or ""),
        str(f.get("category") or ""),
        json.dumps(f.get("oracle") or {}, ensure_ascii=False, default=str),
        json.dumps((f.get("evidence") or {}).get("reproduction_steps") or [], ensure_ascii=False),
    ]
    return " ".join(parts).lower()


def _match(bug: dict, findings: list[dict]) -> dict | None:
    kws = [str(k).lower() for k in (bug.get("match_keywords") or []) if str(k).strip()]
    module = str(bug.get("module") or "").lower()
    best = None
    best_hits = 0
    for f in findings:
        blob = _text_blob(f)
        hits = sum(1 for k in kws if k and k in blob)
        # require module signal OR >=2 keyword hits to count as a match
        module_signal = bool(module) and (module.split("-")[0] in blob)
        if (hits >= 2) or (hits >= 1 and module_signal):
            if hits > best_hits:
                best_hits = hits
                best = f
    return best


def main():
    scan_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("platform_outputs/benchmark_mall_v05_p0probe/scan_result.json")
    gt_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(
        "C:/Users/Test/Desktop/qualibug_enterprise_benchmark_v0_5_windows_native_stable/qualibug_enterprise_benchmark_v0_5_windows_native_stable/hidden_ground_truth"
    )
    scan = _load(scan_path)
    bugs = _load(gt_dir / "bugs.json")
    findings = [f for f in (scan.get("findings") or []) if isinstance(f, dict)]

    matched = []
    for bug in bugs:
        m = _match(bug, findings)
        if m:
            matched.append((bug, m))

    matched_bug_ids = {b["bug_id"] for b, _ in matched}
    matched_finding_titles = {id(m) for _, m in matched}
    unmatched_findings = [f for f in findings if id(f) not in matched_finding_titles]

    total_bugs = len(bugs)
    n_findings = len(findings)
    n_matched = len(matched_bug_ids)

    # evidence completeness on confirmed findings
    def _complete(f: dict) -> bool:
        ev = f.get("evidence") or {}
        steps = ev.get("reproduction_steps") or []
        dbe = f.get("db_evidence") or {}
        has_db = isinstance(dbe, dict) and dbe.get("status") == "captured"
        primary = str(ev.get("request") or "")
        # primary evidence must be a write when repro contains a write
        return bool(steps) and bool(primary) and has_db

    complete = [f for f in findings if _complete(f)]

    print("=" * 66)
    print("QualiBug 靶场评测 (external, ground-truth based)")
    print("=" * 66)
    print(f"scan_id           : {scan.get('scan_id')}")
    print(f"grade             : {scan.get('grade')}   score={scan.get('score')} coverage={scan.get('coverage')}")
    print(f"discovery_verdict : {scan.get('discovery_verdict')}")
    dr = scan.get("dedupe_report") or {}
    print(f"dedupe            : {dr.get('input_count')} -> {dr.get('unique_count')} (collapsed {dr.get('collapsed_count')})")
    dbv = scan.get("db_verification") or {}
    print(f"db_verification   : status={dbv.get('status')} with_db_evidence={dbv.get('findings_with_db_evidence')} with_change={dbv.get('findings_with_db_change')}")
    print("-" * 66)
    print(f"ground-truth bugs : {total_bugs}")
    print(f"findings reported : {n_findings}")
    print(f"matched GT bugs   : {n_matched}  (coverage_rate={n_matched/total_bugs:.1%})")
    print(f"unmatched findings: {len(unmatched_findings)}  (potential false positives)")
    print(f"evidence-complete : {len(complete)}/{n_findings} findings have steps+primary-write+DB snapshot")
    print("-" * 66)
    print("命中的 GT bug:")
    for b, m in matched:
        dbe = m.get("db_evidence") or {}
        chg = dbe.get("changed_tables") if isinstance(dbe, dict) else None
        print(f"  [{b['bug_id']}] {b['title'][:44]:44}  <=  {str(m.get('title'))[:40]}  db_change={bool(chg)}")
    if unmatched_findings:
        print("-" * 66)
        print("未匹配到 GT 的 findings (需人工复核是否误报):")
        for f in unmatched_findings[:15]:
            print(f"  - {str(f.get('title'))[:70]}")
    print("=" * 66)


if __name__ == "__main__":
    main()
