#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Check runtime interface discovery yield in current scan."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
v12 = scan["v12"]

rid = v12.get("runtime_interface_discovery") or {}
print("runtime_interface_discovery type:", type(rid).__name__)
if isinstance(rid, dict):
    print("keys:", sorted(rid.keys()))
    for k in ("candidate_count","candidate_budget","discovered_count","executed_count","selected_count"):
        if k in rid:
            print(f"  {k}: {rid[k]}")
    # plan receipt
    plan = rid.get("plan") or rid.get("discovery_plan") or {}
    if isinstance(plan, dict):
        print("plan.candidate_count:", plan.get("candidate_count"), "budget:", plan.get("candidate_budget"), "unbounded:", plan.get("unbounded_candidate_count"))
    obs = rid.get("observation_receipts") or []
    print("observation_receipts:", len(obs))
    statuses = {}
    for o in obs:
        if isinstance(o, dict):
            statuses[str(o.get("status"))] = statuses.get(str(o.get("status")), 0) + 1
    print("observation statuses:", statuses)
    disc = rid.get("discovered_operations") or []
    print("discovered_operations:", len(disc))
    for d in disc[:30]:
        if isinstance(d, dict):
            print("   DISC:", d.get("method"), d.get("path"))

overlay = v12.get("runtime_source_overlay_receipt") or {}
print("\nruntime_source_overlay_receipt keys:", sorted(overlay.keys()) if isinstance(overlay, dict) else type(overlay))
if isinstance(overlay, dict):
    for k in ("discovered_operation_count","merged_operation_count","total_operation_count"):
        if k in overlay:
            print(f"  {k}: {overlay[k]}")

out = {"rid_keys": sorted(rid.keys()) if isinstance(rid, dict) else None}
Path("_rid_check.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
