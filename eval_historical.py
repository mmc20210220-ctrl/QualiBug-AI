# -*- coding: utf-8 -*-
"""Evaluate historical scan result."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from benchmark_evaluator.benchmark_compute import compute_benchmark

# Load historical scan result
d = json.loads(Path("platform_outputs/benchmark_mall/scan_result.json").read_text(encoding="utf-8"))
findings = d.get("findings", [])
print(f"Historical scan findings: {len(findings)}")

# Run evaluator
gt_path = r"d:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
eval_result = compute_benchmark(
    project="benchmark_mall",
    findings=findings,
    root=Path(r"d:\QualiBug-AI\QualiBug-AI-main"),
    ground_truth_path=gt_path,
)

print(f"\n=== EVALUATOR RESULTS ===")
print(f"  GT bugs: {eval_result.get('ground_truth_bug_count')}")
print(f"  Scan findings: {eval_result.get('scan_findings_total')}")
print(f"  TP: {eval_result.get('true_positives')}")
print(f"  FP: {eval_result.get('false_positives')}")
print(f"  Precision: {eval_result.get('precision')}")
print(f"  Recall: {eval_result.get('recall')}")

matched = eval_result.get("matched_bugs", [])
if matched:
    print(f"\n  Matched GT ({len(matched)}):")
    for m in matched:
        print(f"    - {m.get('gt_bug_id')}: {m.get('gt_title', '')[:50]} (score={m.get('match_score')})")
