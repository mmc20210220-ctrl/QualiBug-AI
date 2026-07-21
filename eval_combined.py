# -*- coding: utf-8 -*-
"""Combined evaluation: scan findings + DB audit findings."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from benchmark_evaluator.benchmark_compute import _match_finding_to_gt, _deduplicate_benchmark_findings

# Load GT
gt_path = r"D:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
with open(gt_path, 'r', encoding='utf-8') as f:
    gt = json.load(f)
truth_bugs = gt if isinstance(gt, list) else gt.get('bugs', [])

# Load existing scan findings
scan_path = r"d:\QualiBug-AI\QualiBug-AI-main\platform_outputs\benchmark_mall\scan_result.json"
with open(scan_path, 'r', encoding='utf-8') as f:
    scan = json.load(f)
v12 = scan.get('v12', scan)
scan_findings = v12.get('findings', v12.get('customer_findings', []))
if not scan_findings:
    scan_findings = scan.get('findings', [])

# Load DB audit findings
with open("db_audit_findings.json", 'r', encoding='utf-8') as f:
    db_findings = json.load(f)

print(f"Scan findings: {len(scan_findings)}")
print(f"DB audit findings: {len(db_findings)}")

# Merge and deduplicate
all_findings = scan_findings + db_findings
all_findings, dup_count = _deduplicate_benchmark_findings(all_findings)
print(f"Combined (after dedup): {len(all_findings)} (removed {dup_count} dups)")

# Evaluate
matched_gt_ids = set()
tp_findings = []
fp_findings = []
for f in all_findings:
    m = _match_finding_to_gt(f, truth_bugs, matched_gt_ids)
    if m:
        gid = m.get('bug_id', m.get('id', ''))
        matched_gt_ids.add(gid)
        tp_findings.append((f, m))
    else:
        fp_findings.append(f)

tp_bugs = len(matched_gt_ids)
tp_count = len(tp_findings)
fp_count = len(fp_findings)
precision = tp_count / (tp_count + fp_count) * 100 if (tp_count + fp_count) > 0 else 0
recall = tp_bugs / len(truth_bugs) * 100

print(f"\n{'='*60}")
print(f"TP bugs (unique GT matched): {tp_bugs}/{len(truth_bugs)}")
print(f"TP findings: {tp_count}")
print(f"FP findings: {fp_count}")
print(f"Precision: {precision:.1f}%")
print(f"Recall: {recall:.2f}%")
print(f"\nMatched GT IDs: {sorted(matched_gt_ids)}")

print(f"\n--- TP details ---")
for f, m in tp_findings:
    gid = m.get('bug_id', m.get('id', ''))
    src = f.get('evidence_source', 'scan')
    print(f"  [{gid}] {m.get('title','')} ← {src}: {f.get('title','')[:60]}")

print(f"\n--- FP details ---")
for f in fp_findings[:10]:
    src = f.get('evidence_source', 'scan')
    print(f"  [{src}] {f.get('title','')[:80]}")
