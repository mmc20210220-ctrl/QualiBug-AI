# -*- coding: utf-8 -*-
"""Check if DB audit TPs are new (not in existing scan TPs)."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from benchmark_evaluator.benchmark_compute import _match_finding_to_gt

# Load GT
gt_path = r"D:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
with open(gt_path, 'r', encoding='utf-8') as f:
    gt = json.load(f)
truth_bugs = gt if isinstance(gt, list) else gt.get('bugs', [])

# Load existing scan findings
scan_path = r"d:\QualiBug-AI\QualiBug-AI-main\platform_outputs\benchmark_mall\scan_result.json"
with open(scan_path, 'r', encoding='utf-8') as f:
    scan = json.load(f)

# Get findings from scan
v12 = scan.get('v12', scan)
findings = v12.get('findings', v12.get('customer_findings', []))
if not findings:
    # Try other locations
    findings = scan.get('findings', [])
print(f"Existing scan findings: {len(findings)}")

# Match existing findings to GT
matched_gt_ids = set()
for f in findings:
    m = _match_finding_to_gt(f, truth_bugs, matched_gt_ids)
    if m:
        gid = m.get('bug_id', m.get('id', ''))
        matched_gt_ids.add(gid)

print(f"Existing scan TP GT IDs ({len(matched_gt_ids)}): {sorted(matched_gt_ids)}")

# Check if DB audit TPs are new
db_tps = ["INV-003", "ORDER-003", "PAY-002"]
for tp in db_tps:
    status = "ALREADY MATCHED" if tp in matched_gt_ids else "NEW TP!"
    print(f"  {tp}: {status}")
