#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Analyze the 30 unmatched (FP) findings after control fix."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
bugs = json.loads(Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json").read_text(encoding="utf-8"))
findings = [f for f in (scan.get("findings") or []) if isinstance(f, dict)]

def _text_blob(f):
    parts = [
        str(f.get("title") or ""),
        str(f.get("expected") or ""),
        str(f.get("actual") or ""),
        str(f.get("category") or ""),
        json.dumps(f.get("oracle") or {}, ensure_ascii=False, default=str),
        json.dumps((f.get("evidence") or {}).get("reproduction_steps") or [], ensure_ascii=False),
    ]
    return " ".join(parts).lower()

def _match(bug, findings):
    kws = [str(k).lower() for k in (bug.get("match_keywords") or []) if str(k).strip()]
    module = str(bug.get("module") or "").lower()
    best = None
    best_hits = 0
    for f in findings:
        blob = _text_blob(f)
        hits = sum(1 for k in kws if k and k in blob)
        module_signal = bool(module) and (module.split("-")[0] in blob)
        if (hits >= 2) or (hits >= 1 and module_signal):
            if hits > best_hits:
                best_hits = hits
                best = f
    return best

matched_finding_ids = set()
for bug in bugs:
    m = _match(bug, findings)
    if m:
        matched_finding_ids.add(id(m))

unmatched = [f for f in findings if id(f) not in matched_finding_ids]
matched = [f for f in findings if id(f) in matched_finding_ids]

out = {
    "total_findings": len(findings),
    "matched_count": len(matched),
    "unmatched_count": len(unmatched),
    "unmatched_details": [],
    "matched_details": [],
}

def summarize(f):
    ev = f.get("evidence") or {}
    oracle = f.get("oracle") or {}
    return {
        "title": str(f.get("title"))[:90],
        "category": f.get("category"),
        "assertion_kind": oracle.get("assertion_kind") or oracle.get("kind"),
        "expected": str(f.get("expected"))[:80],
        "actual": str(f.get("actual"))[:120],
        "control_status": ev.get("control_observation", {}).get("status_code") if isinstance(ev.get("control_observation"), dict) else ev.get("control_status"),
        "treatment_status": ev.get("treatment_observation", {}).get("status_code") if isinstance(ev.get("treatment_observation"), dict) else ev.get("status_code"),
        "control_succeeded": ev.get("control_succeeded"),
        "owner_can_access": ev.get("owner_can_access"),
        "control_actor": ev.get("control_actor_ref"),
        "treatment_actor": ev.get("treatment_actor_ref"),
        "method_path": f"{ev.get('method','')} {ev.get('path_template') or ev.get('path','')}",
    }

for f in unmatched:
    out["unmatched_details"].append(summarize(f))
for f in matched:
    out["matched_details"].append(summarize(f))

Path("_fp_analysis.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"total={len(findings)} matched={len(matched)} unmatched={len(unmatched)}")
print("saved to _fp_analysis.json")
