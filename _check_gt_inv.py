#!/usr/bin/env python
"""Check miss diagnosis for inventory/conservation bugs in GT."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

p = Path("_private_eval/benchmark_mall_131_v1/miss_diagnosis_iteration23_prerequisite_propagation/MISS_DIAGNOSIS.json")
if not p.exists():
    print(f"NOT FOUND: {p}")
    exit(1)

data = json.loads(p.read_text(encoding="utf-8"))
print(f"TP count: {data.get('true_positives')}")
print(f"FP count: {data.get('false_positives')}")
print(f"Recall: {data.get('recall')}")
print(f"Matched IDs: {data.get('matched_bug_ids', [])[:10]}")
print(f"Missed count: {data.get('missed_bug_count')}")

bugs = data.get("miss_reports", [])
print(f"\nTotal miss_reports: {len(bugs)}")

# Search for inventory/conservation
inv_bugs = []
for b in bugs:
    if not isinstance(b, dict):
        continue
    text = json.dumps(b, ensure_ascii=False, default=str).lower()
    if "inventory" in text or "conservation" in text or "INV-" in str(b.get("bug_id", "")):
        inv_bugs.append(b)

print(f"Inventory/conservation missed bugs: {len(inv_bugs)}")
for b in inv_bugs[:10]:
    bug_id = b.get("bug_id", "?")
    title = b.get("title", b.get("description", "?"))[:80]
    apis = b.get("related_apis", [])
    print(f"  {bug_id}: {title}")
    if apis:
        print(f"    APIs: {apis[:3]}")
