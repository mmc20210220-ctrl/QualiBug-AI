#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Diagnostic: extract the REAL route table from all benchmark_mall services.

This reads the target's source code ONLY for offline diagnosis (to determine the
recall ceiling). These routes must NEVER be hardcoded into the detector.
"""
import re, os, json

SVC = r"D:\QualiBug-AI\benchmark_mall\services"
out = {}
total = 0
for svc in sorted(os.listdir(SVC)):
    f = os.path.join(SVC, svc, "src", "index.js")
    if not os.path.exists(f):
        continue
    t = open(f, encoding="utf-8", errors="replace").read()
    routes = re.findall(r"app\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", t)
    # also router-style: router.get(...)
    routes += re.findall(r"router\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", t)
    out[svc] = [{"m": m.upper(), "p": p} for m, p in routes]
    print("=== %s (%d routes) ===" % (svc, len(routes)))
    for m, p in routes:
        print("  %-6s %s" % (m.upper(), p))
    total += len(routes)
print("TOTAL ROUTES:", total)
json.dump(out, open("_routes.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
