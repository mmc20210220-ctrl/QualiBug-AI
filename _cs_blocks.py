#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Analyze BLOCKED_CONFLICTING_SOURCE experiments: risk_family + conflicting op relationship."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
ec = scan["v12"]["experiment_compile"]
blocked = ec.get("blocked_experiments") or []
cs = [e for e in blocked if isinstance(e, dict) and str((e.get("compile_receipt") or {}).get("reason_code")) == "BLOCKED_CONFLICTING_SOURCE"]
print("BLOCKED_CONFLICTING_SOURCE:", len(cs))

fam = {}
detail_counter = {}
for e in cs:
    fam[str(e.get("risk_family"))] = fam.get(str(e.get("risk_family")), 0) + 1
    det = str((e.get("compile_receipt") or {}).get("detail"))
    detail_counter[det] = detail_counter.get(det, 0) + 1
print("risk_family:", json.dumps(fam, ensure_ascii=False))
print("conflict id detail:", json.dumps(detail_counter, ensure_ascii=False))

# The two conflicts: bir_e8746996aab7524c (finance->refund, op bir_5ac20bf28da670d7),
#                     bir_e5b6362e3f15f6f3 (buyer->order, op bir_cd93d22bc648c06d)
# For each blocked exp, which op does it reference and what's its assertion/actor?
by_conflict = {"bir_e8746996aab7524c": [], "bir_e5b6362e3f15f6f3": []}
for e in cs:
    det = str((e.get("compile_receipt") or {}).get("detail"))
    if det in by_conflict:
        # gather assertion kinds + control/treatment presence
        aks = [str(a.get("assertion_kind") or a.get("kind")) for a in (e.get("assertions") or []) if isinstance(a, dict)]
        by_conflict[det].append({
            "obligation_id": e.get("obligation_id"),
            "risk_family": e.get("risk_family"),
            "assertion_kinds": aks,
            "control_present": bool(e.get("control_plan")),
            "treatment_present": bool(e.get("treatment_plan")),
        })

for cid, items in by_conflict.items():
    print(f"\nconflict {cid}: {len(items)} experiments")
    # summarize assertion kinds
    akc = {}
    for it in items:
        for k in it["assertion_kinds"]:
            akc[k] = akc.get(k, 0) + 1
    print("  assertion_kinds:", json.dumps(akc, ensure_ascii=False))
    ctrl = sum(1 for it in items if it["control_present"])
    print(f"  control_present: {ctrl}/{len(items)}")

out = {"total": len(cs), "risk_family": fam, "detail": detail_counter, "by_conflict": by_conflict}
Path("_cs_blocks.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
