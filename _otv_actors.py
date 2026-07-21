#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Inspect control/treatment actor structure of owner_tenant_visibility findings."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
findings = [f for f in (scan.get("findings") or []) if isinstance(f, dict)]

out = {}
for f in findings:
    title = str(f.get("title") or "")
    if "owner_tenant_visibility" not in title:
        continue
    ev = f.get("evidence") or {}
    oracle = f.get("oracle") or {}
    # Try to find control/treatment actor info across structures
    entry = {
        "title": title,
        "evidence_keys": sorted(ev.keys()),
        "control_actor_ref": ev.get("control_actor_ref"),
        "treatment_actor_ref": ev.get("treatment_actor_ref"),
        "control_actor": ev.get("control_actor"),
        "treatment_actor": ev.get("treatment_actor"),
        "actor": ev.get("actor"),
        "owner_can_access": ev.get("owner_can_access"),
        "viewer_can_access": ev.get("viewer_can_access"),
        "control_succeeded": ev.get("control_succeeded"),
    }
    # Look at control/treatment observation dicts
    for key in ("control_observation", "treatment_observation"):
        obs = ev.get(key)
        if isinstance(obs, dict):
            entry[key + "_keys"] = sorted(obs.keys())
            entry[key + "_actor"] = obs.get("actor") or obs.get("actor_ref") or obs.get("actor_id")
            entry[key + "_status"] = obs.get("status_code")
    # oracle assertions
    asserts = oracle.get("assertions") or []
    if asserts and isinstance(asserts, list):
        a0 = asserts[0] if isinstance(asserts[0], dict) else {}
        entry["oracle_assertion_keys"] = sorted(a0.keys())
        entry["oracle_control"] = a0.get("control")
        entry["oracle_treatment"] = a0.get("treatment")
    out[title] = entry

Path("_otv_actors.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
print("owner_tenant_visibility findings analyzed:", len(out))
