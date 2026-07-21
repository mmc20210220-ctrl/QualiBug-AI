#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Compare operation `id` field (ops map key) vs obligation required_operations refs."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
bir = scan["v12"].get("behavior_ir") or {}
ops = bir.get("operations") or []

# What is the `id` field of operations? (this is the ops map key via _index_by_id)
id_fields = []
for o in ops:
    if isinstance(o, dict):
        id_fields.append({"id": o.get("id"), "operation_id": o.get("operation_id"), "method": o.get("method"), "path": o.get("path")})

op_ids = set(str(o["id"]) for o in id_fields if o["id"])
print("num operations:", len(ops))
print("num unique op.id:", len(op_ids))
print("sample op.id values:", sorted(op_ids)[:8])

# Now: what do obligations reference?
obligations = scan["v12"].get("test_obligations") or {}
if isinstance(obligations, dict):
    obls = obligations.get("obligations") or obligations.get("items") or []
else:
    obls = obligations
print("\ntest_obligations type:", type(scan["v12"].get("test_obligations")).__name__, "count:", len(obls))

ref_counter = {}
missing_refs = set()
matched = 0
for ob in obls:
    if not isinstance(ob, dict):
        continue
    req = ob.get("required_operations") or []
    prop = ob.get("property") or {}
    primary = req[0] if req else prop.get("operation_ref")
    if primary:
        primary = str(primary)
        ref_counter[primary] = ref_counter.get(primary, 0) + 1
        if primary in op_ids:
            matched += 1
        else:
            missing_refs.add(primary)

print("obligations with primary ref:", sum(ref_counter.values()))
print("matched in op.id:", matched)
print("missing (not in op.id):", len(missing_refs))
print("sample missing refs:", sorted(missing_refs)[:10])

out = {
    "num_ops": len(ops),
    "num_unique_op_ids": len(op_ids),
    "op_id_samples": sorted(op_ids)[:10],
    "id_fields": id_fields,
    "num_obligations": len(obls),
    "matched": matched,
    "num_missing_refs": len(missing_refs),
    "missing_refs": sorted(missing_refs),
    "ref_counter_top": dict(sorted(ref_counter.items(), key=lambda x: -x[1])[:20]),
}
Path("_opid_match.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
