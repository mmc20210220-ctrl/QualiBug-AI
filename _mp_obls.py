#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Inspect the 240 MISSING_PRIMARY_OPERATION experiments: risk_family + obligation content."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
ec = scan["v12"]["experiment_compile"]
blocked = ec.get("blocked_experiments") or []

mp = [e for e in blocked if isinstance(e, dict) and str((e.get("compile_receipt") or {}).get("reason_code")) == "MISSING_PRIMARY_OPERATION"]
print("MISSING_PRIMARY_OPERATION:", len(mp))

fam = {}
for e in mp:
    f = str(e.get("risk_family"))
    fam[f] = fam.get(f, 0) + 1
print("risk_family:", json.dumps(fam, ensure_ascii=False))

# collect obligation_ids
mp_obids = set(str(e.get("obligation_id")) for e in mp)
print("unique obligation_ids:", len(mp_obids))

# Look up these obligations in obligation_plan
op = ec.get("obligation_plan") or {}
print("\nobligation_plan type:", type(op).__name__)
if isinstance(op, dict):
    print("obligation_plan keys:", sorted(op.keys())[:20])
    obls = op.get("obligations") or op.get("items") or []
else:
    obls = op
print("obligation_plan obligations:", len(obls))

# index by obligation_id
by_id = {}
for o in obls:
    if isinstance(o, dict) and o.get("obligation_id"):
        by_id[str(o["obligation_id"])] = o

# sample 5 of the missing-primary obligations
samples = []
for obid in sorted(mp_obids)[:5]:
    o = by_id.get(obid)
    if o:
        samples.append({
            "obligation_id": obid,
            "risk_family": o.get("risk_family"),
            "required_operations": o.get("required_operations"),
            "property": o.get("property"),
            "subject_refs": o.get("subject_refs"),
            "required_actors": o.get("required_actors"),
        })
    else:
        samples.append({"obligation_id": obid, "NOT_IN_PLAN": True})

out = {"risk_family": fam, "unique_obids": len(mp_obids), "samples": samples}
Path("_mp_obls.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
for s in samples:
    print(json.dumps(s, ensure_ascii=False, default=str)[:400])
