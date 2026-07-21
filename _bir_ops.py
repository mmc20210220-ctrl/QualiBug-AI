#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Resolve blocked-write bir_* operation refs to method+path."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
bir = scan["v12"].get("behavior_ir") or {}
ops = bir.get("operations") or []
print("behavior_ir operations:", len(ops))
if ops:
    print("op0 keys:", sorted(ops[0].keys()))

targets = ["bir_588a7915b5421e5e","bir_ac0e035f9eb4b43a","bir_dff5e016338935e6","bir_3b3a5b5ce2e90655",
           "bir_aad54f95dc2b53f2","bir_6876d72b3664fd4c","bir_9c14fd249f745565","bir_8b84c00bd763ed97",
           "bir_93a22a916e1cbf45","bir_e250926855e614d0","bir_d0448ec47fe7b601","bir_4464589682df2a5f"]
by_id = {}
for o in ops:
    if isinstance(o, dict):
        for k in ("operation_id","id","ref","operation_ref"):
            if o.get(k):
                by_id[str(o[k])] = o
                break

out = {}
for t in targets:
    o = by_id.get(t)
    if o:
        out[t] = {
            "method": o.get("method"),
            "path": o.get("path") or o.get("path_template"),
            "kind": o.get("operation_kind") or o.get("kind"),
            "effects": o.get("effects"),
            "idempotent": o.get("idempotent"),
            "has_delete": None,
        }
    else:
        out[t] = "NOT FOUND"

# Also: build a map of all DELETE operations to see available compensators
deletes = []
for o in ops:
    if isinstance(o, dict) and str(o.get("method","")).upper() == "DELETE":
        deletes.append({"id": o.get("operation_id") or o.get("id"), "path": o.get("path") or o.get("path_template")})
out["_delete_operations"] = deletes

Path("_bir_ops.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
for t in targets:
    v = out[t]
    if isinstance(v, dict):
        print(f"{t}: {v['method']} {v['path']}  kind={v['kind']}")
    else:
        print(f"{t}: {v}")
print("total DELETE ops:", len(deletes))
