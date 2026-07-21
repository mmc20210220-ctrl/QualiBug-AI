#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Sample details for compile-blocked + not-selected obligations."""
import json, os
from collections import Counter, defaultdict

ledger_dir = r"platform_outputs\benchmark_mall_131\discovery_evolution\trace_ledgers\benchmark_mall_131"
ledgers = sorted(((os.path.getmtime(os.path.join(ledger_dir,f)), os.path.join(ledger_dir,f))
                  for f in os.listdir(ledger_dir) if f.endswith(".json")), reverse=True)
data = json.load(open(ledgers[0][1], encoding="utf-8"))
attempts = data.get("attempts", [])

def sample(reason, n=12):
    rows = [a for a in attempts if a.get("reason_code") == reason]
    return {
        "count": len(rows),
        "samples": [
            {"obligation_id": str(a.get("obligation_id",""))[:45],
             "detail": str(a.get("detail",""))[:200],
             "hypothesis": str(a.get("hypothesis_id") or a.get("hypothesis") or "")[:60],
             "assertion_kind": str(a.get("assertion_kind") or (a.get("obligation") or {}).get("assertion_kind") or "")[:40]}
            for a in rows[:n]
        ],
    }

out = {
    "BLOCKED_MISSING_BINDING": sample("BLOCKED_MISSING_BINDING"),
    "BLOCKED_MISSING_OBSERVER": sample("BLOCKED_MISSING_OBSERVER"),
    "BLOCKED_MISSING_FIXTURE": sample("BLOCKED_MISSING_FIXTURE"),
    "BLOCKED_CONFLICTING_SOURCE": sample("BLOCKED_CONFLICTING_SOURCE"),
    "OBLIGATION_NOT_IN_PLAN": sample("OBLIGATION_NOT_IN_PLAN"),
    "BLOCKED_NON_REVERSIBLE_WRITE": sample("BLOCKED_NON_REVERSIBLE_WRITE"),
}
# Also: what assertion_kinds are in NOT_SELECTED and NON_REVERSIBLE?
for reason in ("OBLIGATION_NOT_IN_PLAN","BLOCKED_NON_REVERSIBLE_WRITE","BLOCKED_MISSING_BINDING"):
    rows = [a for a in attempts if a.get("reason_code") == reason]
    kinds = Counter()
    for a in rows:
        obl = a.get("obligation") or {}
        k = str(a.get("assertion_kind") or obl.get("assertion_kind") or obl.get("property",{}).get("assertion_kind") or "?")
        kinds[k] += 1
    out[reason+"_kinds"] = dict(kinds)

json.dump(out, open("_blockers3.json","w",encoding="utf-8"), indent=2, ensure_ascii=False, default=str)
print("done; keys:", list(out.keys()))
