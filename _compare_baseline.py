#!/usr/bin/env python
"""Compare scan results with baseline."""
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

baseline = json.load(open('generalization_baseline.json', encoding='utf-8'))
scan = json.load(open('_scan_result_p13.json', encoding='utf-8'))

print("=" * 60)
print("REGRESSION COMPARISON: Baseline vs Current")
print("=" * 60)

b_scan = baseline['project_a_scan']
print(f"\n{'Metric':<25} {'Baseline':<15} {'Current':<15} {'Status'}")
print("-" * 60)
print(f"{'Total findings':<25} {b_scan['total_findings']:<15} {scan.get('total_findings', 0):<15} {'OK' if scan.get('total_findings') == b_scan['total_findings'] else 'DIFF'}")
print(f"{'Grade':<25} {b_scan['grade']:<15} {scan.get('grade', '?'):<15} {'OK' if scan.get('grade') == b_scan['grade'] else 'DIFF'}")

# Categories
b_cats = b_scan['categories']
s_cats = {}
for f in scan.get('findings', []):
    cat = f.get('category', '?')
    s_cats[cat] = s_cats.get(cat, 0) + 1

print(f"\n{'Category':<25} {'Baseline':<15} {'Current':<15} {'Status'}")
print("-" * 60)
all_cats = sorted(set(list(b_cats.keys()) + list(s_cats.keys())))
for cat in all_cats:
    bv = b_cats.get(cat, 0)
    sv = s_cats.get(cat, 0)
    status = 'OK' if bv == sv else ('GAIN' if sv > bv else 'LOSS')
    print(f"{cat:<25} {bv:<15} {sv:<15} {status}")

# Deep business TP
print(f"\n{'='*60}")
print("DEEP BUSINESS TP CHECK")
print(f"{'='*60}")
print(f"Baseline deep_business_tp: {b_scan['deep_business_tp']}")

conservation = [f for f in scan.get('findings', []) if f.get('category') == 'conservation']
print(f"Current conservation findings: {len(conservation)}")
for f in conservation:
    print(f"  - {f.get('title', '?')[:80]}")

# TP retention
total_baseline = b_scan['total_findings']
total_current = scan.get('total_findings', 0)
retention = (total_current / total_baseline * 100) if total_baseline > 0 else 0
print(f"\n{'='*60}")
print(f"TP RETENTION RATE: {retention:.1f}% (target: >=90%)")
print(f"{'='*60}")

# Check terms=[]
empty_terms = 0
for f in scan.get('findings', []):
    assertions = f.get('failed_assertions', [])
    for a in assertions:
        if isinstance(a, dict) and a.get('terms') == []:
            empty_terms += 1
print(f"Findings with terms=[]: {empty_terms} (target: 0)")
