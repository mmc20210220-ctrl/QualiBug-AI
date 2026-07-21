#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Test hypothesis: owner_tenant_visibility FP iff treatment actor == resource owner."""
import json, re
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
bugs = json.loads(Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json").read_text(encoding="utf-8"))
findings = [f for f in (scan.get("findings") or []) if isinstance(f, dict)]

# Reproduce evaluator match to label TP/FP
def _text_blob(f):
    parts = [str(f.get("title") or ""), str(f.get("expected") or ""), str(f.get("actual") or ""),
             str(f.get("category") or ""), json.dumps(f.get("oracle") or {}, ensure_ascii=False, default=str),
             json.dumps((f.get("evidence") or {}).get("reproduction_steps") or [], ensure_ascii=False)]
    return " ".join(parts).lower()
def _match(bug, findings):
    kws = [str(k).lower() for k in (bug.get("match_keywords") or []) if str(k).strip()]
    module = str(bug.get("module") or "").lower()
    best, best_hits = None, 0
    for f in findings:
        blob = _text_blob(f)
        hits = sum(1 for k in kws if k and k in blob)
        msig = bool(module) and (module.split("-")[0] in blob)
        if (hits >= 2) or (hits >= 1 and msig):
            if hits > best_hits:
                best_hits, best = hits, f
    return best
tp_ids = set()
for bug in bugs:
    m = _match(bug, findings)
    if m: tp_ids.add(id(m))

rows = []
for f in findings:
    title = str(f.get("title") or "")
    if "owner_tenant_visibility" not in title:
        continue
    ev = f.get("evidence") or {}
    assertion = ev.get("assertion") or {}
    treatment_actor = str(ev.get("actor") or "")
    assertion_id = str(assertion.get("assertion_id") or "")
    # owner from source_refs
    owner_class = ""
    runtime_actor = ""
    for sr in (assertion.get("source_refs") or []):
        kind = str(sr.get("kind") or "")
        loc = str(sr.get("locator") or "")
        if kind == "permission_matrix" and not owner_class:
            owner_class = loc
        if kind == "runtime_actor" and not runtime_actor:
            runtime_actor = loc
    runtime_class = runtime_actor.split(":")[0] if runtime_actor else ""
    is_self = (treatment_actor.lower() == runtime_class.lower()) if runtime_class else None
    rows.append({
        "title": title.replace("[ContractOracle] owner_tenant_visibility: ", ""),
        "label": "TP" if id(f) in tp_ids else "FP",
        "treatment_actor": treatment_actor,
        "assertion_id": assertion_id,
        "owner_class_permmatrix": owner_class,
        "runtime_actor": runtime_actor,
        "runtime_class": runtime_class,
        "treatment_is_owner": is_self,
    })

Path("_otv_hypothesis.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
for r in rows:
    print(f"{r['label']} | self={r['treatment_is_owner']} | aid={r['assertion_id']:18s} | treat={r['treatment_actor']:9s} owner={r['runtime_class']:9s} | {r['title']}")
