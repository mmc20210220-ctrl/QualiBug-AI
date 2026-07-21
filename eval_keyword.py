# -*- coding: utf-8 -*-
"""Evaluate using keyword matcher (matcher.py)."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from benchmark_evaluator.matcher import match_bug, normalize_api

# Load current scan result
d = json.loads(Path("scan_fresh_result.json").read_text(encoding="utf-8"))
findings = d.get("findings", [])
print(f"Scan findings: {len(findings)}")

# Load GT
gt_path = Path(r"d:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json")
gt_bugs = json.loads(gt_path.read_text(encoding="utf-8"))
if isinstance(gt_bugs, dict):
    gt_bugs = gt_bugs.get("bugs", [])
print(f"GT bugs: {len(gt_bugs)}")

# Match findings to GT
used = set()
matched = []
for f in findings:
    # Convert finding to matcher format
    discovered = {
        "title": f.get("title", ""),
        "risk_type": f.get("category", ""),
        "severity": f.get("severity", ""),
        "related_apis": [],
        "expected": f.get("expected", ""),
        "actual": f.get("actual", ""),
    }
    
    # Extract API from evidence
    evidence = f.get("evidence", {})
    if isinstance(evidence, dict):
        request = evidence.get("request", "")
        if request:
            discovered["related_apis"].append(request)
    
    # Try to match
    result = match_bug(discovered, gt_bugs, used)
    if result:
        gt_id = result.get("bug_id") or result.get("bug_instance_id")
        used.add(gt_id)
        matched.append({
            "finding_title": f.get("title", "")[:60],
            "gt_bug_id": gt_id,
            "gt_title": result.get("title", "")[:50],
            "match_score": result.get("__match_score"),
            "match_type": result.get("__match_type"),
        })

print(f"\n=== KEYWORD MATCHER RESULTS ===")
print(f"  TP: {len(matched)}")
print(f"  FP: {len(findings) - len(matched)}")
print(f"  Precision: {len(matched) / len(findings) if findings else 0:.4f}")
print(f"  Recall: {len(matched) / len(gt_bugs) if gt_bugs else 0:.4f}")

if matched:
    print(f"\n  Matched GT ({len(matched)}):")
    for m in matched:
        print(f"    - {m['gt_bug_id']}: {m['gt_title']} (score={m['match_score']}, type={m['match_type']})")
