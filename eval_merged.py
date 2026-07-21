# -*- coding: utf-8 -*-
"""Re-evaluate with merged scan + DB findings."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")
from pathlib import Path
from benchmark_evaluator.benchmark_compute import compute_benchmark

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
findings = data.get('findings', [])
db_findings = data.get('db_findings', [])
all_findings = list(findings) + list(db_findings)

print(f"Scan findings: {len(findings)}")
print(f"DB findings: {len(db_findings)}")
print(f"Total for evaluation: {len(all_findings)}")

gt_path = r"d:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
root = Path(r"d:\QualiBug-AI\QualiBug-AI-main")

eval_result = compute_benchmark(
    project="benchmark_mall",
    findings=all_findings,
    root=root,
    ground_truth_path=gt_path,
)

print(f"\n=== EVALUATOR RESULTS (merged) ===")
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
        print(f"    - {m.get('gt_bug_id')}: {m.get('gt_title', '')[:60]} (score={m.get('match_score')})")

# Also evaluate scan-only for comparison
eval_scan_only = compute_benchmark(
    project="benchmark_mall",
    findings=findings,
    root=root,
    ground_truth_path=gt_path,
)
print(f"\n=== SCAN-ONLY (comparison) ===")
print(f"  TP: {eval_scan_only.get('true_positives')}")
print(f"  FP: {eval_scan_only.get('false_positives')}")
print(f"  Precision: {eval_scan_only.get('precision')}")
