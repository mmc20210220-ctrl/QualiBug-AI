#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Decisive: how many missed bugs are on suite-documented paths vs hidden paths."""
import json, re
from pathlib import Path

# Parse suite openapi.yaml paths (documented surface)
suite_input = Path(r"D:\QualiBug-AI\benchmark_suite_v3\QualiBug_Benchmark_Suite_v3\projects\01_ecommerce_order_payment_inventory\input")
yaml_text = (suite_input / "openapi.yaml").read_text(encoding="utf-8", errors="replace")
doc_paths = set()
for line in yaml_text.splitlines():
    m = re.match(r"^\s{2}(/[A-Za-z0-9_\-/{}.:]+):", line)
    if m:
        doc_paths.add(m.group(1))
print("suite documented paths:", len(doc_paths))

def norm(p):
    p = str(p).split("?",1)[0].rstrip("/").lower()
    p = re.sub(r"/qb_test_[0-9a-z]+", "/*", p)
    p = re.sub(r"/qb-test-[0-9a-f]+", "/*", p)
    p = re.sub(r"\{[^}]+\}", "/*", p)
    p = re.sub(r"/[0-9a-f]{8,}", "/*", p)
    return p
doc_norm = {norm(p) for p in doc_paths}

rep = json.loads(Path("_miss_diagnosis.json").read_text(encoding="utf-8"))
misses = rep.get("miss_reports") or []
print("miss reports:", len(misses))

on_doc = 0
on_hidden = 0
no_path = 0
hidden_paths = {}
doc_hit_paths = {}
for m in misses:
    rps = (m.get("detail") or {}).get("related_paths") or []
    if not rps:
        no_path += 1
        continue
    hit_doc = False
    for p in rps:
        np = norm(p)
        if np in doc_norm:
            hit_doc = True
            doc_hit_paths[np] = doc_hit_paths.get(np, 0) + 1
        else:
            hidden_paths[np] = hidden_paths.get(np, 0) + 1
    if hit_doc:
        on_doc += 1
    else:
        on_hidden += 1

print(f"\nmissed bugs on DOCUMENTED paths: {on_doc}")
print(f"missed bugs on HIDDEN paths only: {on_hidden}")
print(f"missed bugs with no related_paths: {no_path}")
print(f"\ndistinct hidden paths: {len(hidden_paths)}")
print("top hidden paths:")
for p, c in sorted(hidden_paths.items(), key=lambda x: -x[1])[:40]:
    print(f"  {c:2d}  {p}")

out = {"doc_paths": sorted(doc_paths), "on_doc": on_doc, "on_hidden": on_hidden, "no_path": no_path, "hidden_paths": hidden_paths}
Path("_doc_vs_hidden.json").write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
