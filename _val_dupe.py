#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compare canonical identities of duplicate validation_rejection findings."""
import json
from pathlib import Path
from collections import defaultdict

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
registry = scan.get("canonical_defect_registry") or {}
findings = scan.get("findings") or []

by_title = defaultdict(list)
for f in findings:
    by_title[str(f.get("title"))].append(f)

out = {}
# Find duplicate titles
for title, rows in by_title.items():
    if len(rows) > 1 and "validation_rejection" in title:
        cdefs = [r.get("canonical_defect_id") for r in rows]
        idents = {}
        for cd in registry.get("canonical_defects", []):
            if cd.get("canonical_defect_id") in cdefs:
                idents[cd["canonical_defect_id"]] = cd.get("identity")
        # Compare
        keys = list(idents.keys())
        diffs = {}
        if len(keys) >= 2:
            a, b = idents[keys[0]], idents[keys[1]]
            for field in a:
                if a.get(field) != b.get(field):
                    diffs[field] = {"A": a.get(field), "B": b.get(field)}
        out[title] = {"count": len(rows), "cdefs": cdefs, "diffs": diffs}

Path("_val_dupe.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print("titles analyzed:", len(out))
