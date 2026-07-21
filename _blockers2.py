#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Comprehensive blocker distribution analysis from latest trace ledger."""
import json, os
from collections import Counter, defaultdict

ledger_dir = r"platform_outputs\benchmark_mall_131\discovery_evolution\trace_ledgers\benchmark_mall_131"
ledgers = []
for f in os.listdir(ledger_dir):
    if f.endswith(".json"):
        p = os.path.join(ledger_dir, f)
        ledgers.append((os.path.getmtime(p), p))
ledgers.sort(reverse=True)
latest = ledgers[0][1]

data = json.load(open(latest, encoding="utf-8"))
attempts = data.get("attempts", [])

out = {
    "ledger": os.path.basename(latest),
    "total_attempts": len(attempts),
    "terminal_status": dict(Counter(a.get("terminal_status") for a in attempts)),
    "reason_code": dict(Counter(a.get("reason_code") for a in attempts)),
}

# Focus on compile-blocked
compile_blocked = [a for a in attempts if "COMPILE" in str(a.get("terminal_status","")) or "COMPILE" in str(a.get("reason_code",""))]
out["compile_blocked_count"] = len(compile_blocked)
cb_reasons = Counter(a.get("reason_code") for a in compile_blocked)
out["compile_blocked_reasons"] = dict(cb_reasons)
# sample details
out["compile_blocked_samples"] = [
    {"obligation_id": a.get("obligation_id","")[:40], "terminal": a.get("terminal_status"),
     "reason": a.get("reason_code"), "detail": str(a.get("detail",""))[:160]}
    for a in compile_blocked[:20]
]

# NOT_SELECTED
not_sel = [a for a in attempts if "NOT_SELECTED" in str(a.get("terminal_status","")) or "NOT_SELECTED" in str(a.get("reason_code",""))]
out["not_selected_count"] = len(not_sel)

# NON_REVERSIBLE
non_rev = [a for a in attempts if "REVERSIBLE" in str(a.get("reason_code","")) or "CLEANUP" in str(a.get("terminal_status","")) or "CLEANUP" in str(a.get("reason_code",""))]
out["cleanup_blocked_count"] = len(non_rev)
out["cleanup_blocked_reasons"] = dict(Counter(a.get("reason_code") for a in non_rev))

json.dump(out, open("_blockers.json","w",encoding="utf-8"), indent=2, ensure_ascii=False)
print("ledger:", os.path.basename(latest))
print("total_attempts:", len(attempts))
print("terminal_status:", out["terminal_status"])
print("reason_code:", out["reason_code"])
