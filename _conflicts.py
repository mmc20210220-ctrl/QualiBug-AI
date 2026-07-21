#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Inspect behavior_ir conflicts to judge whether BLOCKED_CONFLICTING_SOURCE is releasable."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
bir = scan["v12"].get("behavior_ir") or {}
conflicts = bir.get("conflicts") or []
print("total conflicts:", len(conflicts))

out = {"total": len(conflicts), "by_status": {}, "samples": []}
for c in conflicts:
    if not isinstance(c, dict):
        continue
    st = str(c.get("status"))
    out["by_status"][st] = out["by_status"].get(st, 0) + 1

# show first 10 conflicting ones with full structure
shown = 0
for c in conflicts:
    if isinstance(c, dict) and str(c.get("status")) == "conflicting":
        out["samples"].append(c)
        shown += 1
        if shown >= 10:
            break

# also collect the set of conflicting operation_refs
oprefs = set()
for c in conflicts:
    if isinstance(c, dict) and str(c.get("status")) == "conflicting":
        r = c.get("operation_ref")
        if r:
            oprefs.add(str(r))
out["conflicting_operation_refs"] = sorted(oprefs)
out["num_conflicting_oprefs"] = len(oprefs)

Path("_conflicts.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print("by_status:", out["by_status"])
print("num conflicting oprefs:", len(oprefs))
for s in out["samples"][:3]:
    print(json.dumps(s, ensure_ascii=False, default=str)[:600])
