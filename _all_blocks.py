#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Analyze all_experiments + block_reason_counts + blocked_experiments."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
ec = scan["v12"]["experiment_compile"]

print("compiled_count:", ec.get("compiled_count"))
print("blocked_count:", ec.get("blocked_count"))
print("block_reason_counts:", json.dumps(ec.get("block_reason_counts"), ensure_ascii=False))
all_exps = ec.get("all_experiments") or []
print("all_experiments:", len(all_exps))
blocked = ec.get("blocked_experiments") or []
print("blocked_experiments:", len(blocked))

# For blocked ones, tally reason + collect detail/obligation refs
reason = {}
missing_primary_detail = {}
for e in blocked:
    if not isinstance(e, dict):
        continue
    rec = e.get("compile_receipt") or {}
    rc = str(rec.get("reason_code")) or str(e.get("block_reason"))
    reason[rc] = reason.get(rc, 0) + 1
    if rc == "MISSING_PRIMARY_OPERATION":
        det = str(rec.get("detail"))
        missing_primary_detail[det] = missing_primary_detail.get(det, 0) + 1

print("\nblocked reason tally:", json.dumps(reason, ensure_ascii=False))
print("\nMISSING_PRIMARY_OPERATION detail distribution:")
for k, v in sorted(missing_primary_detail.items(), key=lambda x: -x[1])[:25]:
    print(f"  {v:4d}  {k}")

# sample one blocked experiment structure
if blocked:
    sample = blocked[0]
    print("\nblocked sample keys:", sorted(sample.keys()))

out = {
    "compiled_count": ec.get("compiled_count"),
    "blocked_count": ec.get("blocked_count"),
    "block_reason_counts": ec.get("block_reason_counts"),
    "all_experiments": len(all_exps),
    "blocked_experiments": len(blocked),
    "reason_tally": reason,
    "missing_primary_detail": missing_primary_detail,
}
Path("_all_blocks.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
