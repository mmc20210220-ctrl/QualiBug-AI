# -*- coding: utf-8 -*-
"""Analyze GT coverage by module."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from collections import Counter

# Load GT
gt_path = Path(r"d:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json")
gt_bugs = json.loads(gt_path.read_text(encoding="utf-8"))
if isinstance(gt_bugs, dict):
    gt_bugs = gt_bugs.get("bugs", [])

# Count by module
module_counts = Counter(b.get("module", "unknown") for b in gt_bugs)
print("GT bugs by module:")
for module, count in module_counts.most_common():
    print(f"  {module}: {count}")

# Load current scan result
d = json.loads(Path("scan_fresh_result.json").read_text(encoding="utf-8"))
findings = d.get("findings", [])

# Matched GT IDs from evaluator
matched_ids = {"AUTH-003", "PRODUCT-013", "COUPON-011", "AUTH-006", "AUTH-001", "INV-003", "ORDER-003", "PAY-002"}

# Check which modules are covered
covered_modules = set()
for b in gt_bugs:
    if b.get("bug_id") in matched_ids:
        covered_modules.add(b.get("module"))

print(f"\nCovered modules: {covered_modules}")
print(f"Uncovered modules: {set(module_counts.keys()) - covered_modules}")

# Show uncovered bugs by module
print("\nUncovered bugs by module:")
for module in sorted(set(module_counts.keys()) - covered_modules):
    bugs = [b for b in gt_bugs if b.get("module") == module]
    print(f"\n  {module} ({len(bugs)} bugs):")
    for b in bugs[:3]:
        print(f"    - {b.get('bug_id')}: {b.get('title', '')[:50]}")
