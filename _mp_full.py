#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Dump full structure of MISSING_PRIMARY_OPERATION blocked experiments."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
ec = scan["v12"]["experiment_compile"]
blocked = ec.get("blocked_experiments") or []
mp = [e for e in blocked if isinstance(e, dict) and str((e.get("compile_receipt") or {}).get("reason_code")) == "MISSING_PRIMARY_OPERATION"]

# Dump 3 full: one invariant, one state_integrity
inv = next((e for e in mp if e.get("risk_family") == "invariant"), None)
st = next((e for e in mp if e.get("risk_family") == "state_integrity"), None)
samples = [x for x in (inv, st) if x]
Path("_mp_full.json").write_text(json.dumps(samples, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print("dumped", len(samples), "samples")

# Also: what assertion_kinds do these carry?
ak = {}
for e in mp:
    for a in (e.get("assertions") or []):
        if isinstance(a, dict):
            k = str(a.get("assertion_kind") or a.get("kind"))
            ak[k] = ak.get(k, 0) + 1
print("assertion_kinds in MISSING_PRIMARY:", json.dumps(ak, ensure_ascii=False))

# source_refs kinds
sk = {}
for e in mp:
    for s in (e.get("source_refs") or []):
        if isinstance(s, dict):
            sk[str(s.get("kind"))] = sk.get(str(s.get("kind")), 0) + 1
print("source_ref kinds:", json.dumps(sk, ensure_ascii=False))
