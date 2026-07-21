#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Analyze v12.experiment_compile block reasons from current scan_result."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
v12 = scan["v12"]
print("v12 keys:", sorted(v12.keys()))

ec = v12.get("experiment_compile") or {}
print("\nexperiment_compile type:", type(ec).__name__)
if isinstance(ec, dict):
    print("experiment_compile keys:", sorted(ec.keys()))
    exps = ec.get("experiments") or ec.get("items") or []
else:
    exps = ec
print("num experiments:", len(exps))

# Tally compile receipt status + reason_code
status_counter = {}
reason_counter = {}
deferred_refs = set()
for e in exps:
    if not isinstance(e, dict):
        continue
    rec = e.get("compile_receipt") or {}
    st = str(rec.get("status"))
    rc = str(rec.get("reason_code"))
    status_counter[st] = status_counter.get(st, 0) + 1
    if st != "COMPILED":
        reason_counter[rc] = reason_counter.get(rc, 0) + 1
    if rc == "MISSING_PRIMARY_OPERATION":
        # what obligation / primary op?
        obid = e.get("obligation_id")
        det = rec.get("detail")
        deferred_refs.add(str(det))

print("\nstatus:", status_counter)
print("block reasons:", reason_counter)
print("MISSING_PRIMARY_OPERATION details (primary op ids):", sorted(deferred_refs)[:20])

out = {
    "v12_keys": sorted(v12.keys()),
    "num_experiments": len(exps),
    "status": status_counter,
    "reasons": reason_counter,
    "missing_primary_details": sorted(deferred_refs),
}
Path("_compile_blocks.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
