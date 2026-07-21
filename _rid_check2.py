#!/usr/bin/env python
# -*- coding: utf-8 -*-
import json
from pathlib import Path
scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
rid = scan["v12"]["runtime_interface_discovery"]
print("status:", rid.get("status"))
plan = rid.get("plan") or {}
print("plan keys:", sorted(plan.keys()) if isinstance(plan, dict) else type(plan))
if isinstance(plan, dict):
    for k in ("candidate_count","candidate_budget","unbounded_candidate_count","source_resource_count","policy_action_count","truncated","documented_operation_count"):
        print(f"  plan.{k}: {plan.get(k)}")
    cands = plan.get("candidates") or []
    print("  plan.candidates:", len(cands))
    deriv = {}
    for c in cands:
        if isinstance(c, dict):
            deriv[str(c.get("derivation"))] = deriv.get(str(c.get("derivation")), 0) + 1
    print("  derivations:", deriv)
    print("  sample paths:", [c.get("path") for c in cands[:15]])
ex = rid.get("execution") or {}
print("\nexecution keys:", sorted(ex.keys()) if isinstance(ex, dict) else type(ex))
if isinstance(ex, dict):
    for k in ("selected_count","executed_count","blocked_count","harness_failure_count"):
        print(f"  exec.{k}: {ex.get(k)}")
    obs = ex.get("observation_receipts") or []
    st = {}
    for o in obs:
        if isinstance(o, dict):
            st[str(o.get("status"))] = st.get(str(o.get("status")), 0) + 1
    print("  observation statuses:", st)
    disc = ex.get("discovered_operations") or []
    print("  discovered_operations:", len(disc))
    for d in disc[:40]:
        if isinstance(d, dict):
            print("    DISC:", d.get("method"), d.get("path"))
