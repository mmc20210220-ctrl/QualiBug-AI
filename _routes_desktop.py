#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Extract REAL route table from the ACTUAL running target (Desktop v0.5)."""
import re, os, json
SVC = r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable\qualibug_enterprise_benchmark_v0_5_windows_native_stable\services"
out = {}; total = 0
for svc in sorted(os.listdir(SVC)):
    f = os.path.join(SVC, svc, "src", "index.js")
    if not os.path.exists(f):
        continue
    t = open(f, encoding="utf-8", errors="replace").read()
    routes = re.findall(r"app\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", t)
    routes += re.findall(r"router\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)['\"]", t)
    out[svc] = [{"m": m.upper(), "p": p} for m, p in routes]
    total += len(routes)
    print("=== %s (%d) ===" % (svc, len(routes)))
    for m, p in routes:
        print("  %-6s %s" % (m.upper(), p))
print("TOTAL:", total)
json.dump(out, open("_routes_desktop.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
