#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Inspect canonical identities of duplicate findings."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))

print("top-level keys:", sorted(scan.keys()))
registry = scan.get("canonical_defect_registry") or {}
print("\nregistry keys:", sorted(registry.keys()) if registry else "EMPTY")
print("canonical_defect_count:", registry.get("canonical_defect_count"))
print("delivery_occurrence_count:", registry.get("delivery_occurrence_count"))

# Group findings by title
findings = scan.get("findings") or []
from collections import defaultdict
by_title = defaultdict(list)
for f in findings:
    by_title[str(f.get("title"))].append(f)

print("\n=== Duplicate titles ===")
for title, rows in sorted(by_title.items()):
    if len(rows) > 1:
        print(f"\n[{len(rows)}x] {title[:70]}")
        for r in rows:
            print(f"   cdef={r.get('canonical_defect_id')} occ_count={r.get('delivery_occurrence_count')}")

# Now inspect identities of one duplicate group from registry
print("\n=== Identity comparison for a duplicate group ===")
dup_titles = [t for t, rows in by_title.items() if len(rows) > 1]
if dup_titles and registry.get("canonical_defects"):
    target_title = dup_titles[0]
    target_cdefs = {r.get("canonical_defect_id") for r in by_title[target_title]}
    print(f"Title: {target_title[:70]}")
    print(f"cdef ids: {target_cdefs}")
    idents = []
    for cd in registry.get("canonical_defects", []):
        if cd.get("canonical_defect_id") in target_cdefs:
            idents.append(cd.get("identity"))
    # Compare field by field
    if len(idents) >= 2:
        base = idents[0]
        for i, other in enumerate(idents[1:], 1):
            print(f"\n--- identity[0] vs identity[{i}] ---")
            for key in base:
                if base.get(key) != other.get(key):
                    print(f"  DIFF {key}:")
                    print(f"    [0]: {json.dumps(base.get(key), ensure_ascii=False)[:200]}")
                    print(f"    [{i}]: {json.dumps(other.get(key), ensure_ascii=False)[:200]}")
