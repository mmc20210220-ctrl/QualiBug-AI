# -*- coding: utf-8 -*-
"""Analyze FP patterns from scan results."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
from benchmark_evaluator.benchmark_compute import compute_benchmark

# Load scan result
d = json.loads(Path("scan_fresh_result.json").read_text(encoding="utf-8"))
findings = d.get("findings", [])

# Run evaluator to get TP/FP classification
gt_path = r"d:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
eval_result = compute_benchmark(
    project="benchmark_mall",
    findings=findings,
    root=Path(r"d:\QualiBug-AI\QualiBug-AI-main"),
    ground_truth_path=gt_path,
)

matched_gt_ids = set(eval_result.get("matched_bug_ids", []))
print(f"Matched GT IDs: {matched_gt_ids}")
print(f"TP: {eval_result.get('true_positives')}")
print(f"FP: {eval_result.get('false_positives')}")

# Classify findings
confirmed = [f for f in findings if f.get("gate_passed") or f.get("confirmation_status") == "confirmed"]
print(f"\nTotal confirmed findings: {len(confirmed)}")

# Analyze each finding
print("\n" + "=" * 80)
print("FINDING ANALYSIS")
print("=" * 80)

for i, f in enumerate(confirmed):
    title = f.get("title", "?")
    category = f.get("category", "?")
    evidence = f.get("evidence_source", "?")
    
    # Check if this finding matched a GT
    is_tp = False
    for m in eval_result.get("matched_bugs", []):
        if m.get("finding_title") == title:
            is_tp = True
            gt_id = m.get("gt_bug_id")
            score = m.get("match_score")
            print(f"\n[{i+1}] TP → {gt_id} (score={score})")
            break
    
    if not is_tp:
        print(f"\n[{i+1}] FP")
    
    print(f"    Title: {title[:80]}")
    print(f"    Category: {category}")
    print(f"    Evidence: {evidence}")
    print(f"    Severity: {f.get('severity', '?')}")
    print(f"    Confidence: {f.get('confidence', '?')}")

# Summary by category
print("\n" + "=" * 80)
print("FP BY CATEGORY")
print("=" * 80)

fp_by_cat = {}
for f in confirmed:
    title = f.get("title", "")
    is_tp = any(m.get("finding_title") == title for m in eval_result.get("matched_bugs", []))
    if not is_tp:
        cat = f.get("category", "unknown")
        fp_by_cat.setdefault(cat, []).append(title[:60])

for cat, titles in sorted(fp_by_cat.items()):
    print(f"\n{cat}: {len(titles)} FPs")
    for t in titles[:5]:
        print(f"  - {t}")
