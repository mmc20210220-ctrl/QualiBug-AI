#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Decisive: are the missed GT bug paths actually reachable routes on the real
benchmark_mall target (gateway:8080)?

Compares miss_diagnosis related_paths against the REAL route table extracted
from the microservice source (diagnostic only - never hardcoded in detector).
"""
import json, re
from pathlib import Path

routes = json.loads(Path("_routes.json").read_text(encoding="utf-8"))
prefix_by_svc = {
    "auth-service": "/api/auth",
    "user-service": "/api/users",
    "product-service": "/api/products",
    "inventory-service": "/api/inventory",
    "cart-service": "/api/cart",
    "coupon-service": "/api/coupons",
    "order-service": "/api/orders",
    "payment-service": "/api/payments",
    "refund-service": "/api/refunds",
    "report-service": "/api/reports",
}

# Build full-path route patterns (method, regex)
full = []
for svc, rs in routes.items():
    pre = prefix_by_svc.get(svc)
    if not pre:
        continue
    for r in rs:
        p = r["p"]
        if p == "/health":
            continue
        # normalize trailing-slash root path: pre + '/' -> pre
        fullpath = (pre + p) if p != "/" else pre
        # :param -> [^/]+  (re.escape does NOT escape ':', so match bare colon)
        rx = re.escape(fullpath)
        rx = re.sub(r":[A-Za-z0-9_]+", r"[^/]+", rx)
        full.append((r["m"], re.compile("^" + rx + "$"), fullpath))

print("real business routes:", len(full))

def match_route(path):
    path = str(path).split("?", 1)[0].rstrip("/")
    hits = []
    for m, rx, fp in full:
        if rx.match(path):
            hits.append((m, fp))
    return hits

rep = json.loads(Path("_miss_diagnosis.json").read_text(encoding="utf-8"))
misses = rep.get("miss_reports") or []
print("miss reports:", len(misses))

reachable = 0
unreachable = 0
no_path = 0
unreach_paths = {}
reach_paths = {}
for m in misses:
    rps = (m.get("detail") or {}).get("related_paths") or []
    if not rps:
        no_path += 1
        continue
    any_hit = False
    for p in rps:
        hits = match_route(p)
        if hits:
            any_hit = True
            reach_paths[p] = reach_paths.get(p, 0) + 1
        else:
            unreach_paths[p] = unreach_paths.get(p, 0) + 1
    if any_hit:
        reachable += 1
    else:
        unreachable += 1

print("\nmissed bugs with a REACHABLE real route:", reachable)
print("missed bugs ONLY on NON-EXISTENT routes:", unreachable)
print("missed bugs with no related_paths:", no_path)
print("\ndistinct reachable missed paths:", len(reach_paths))
for p, c in sorted(reach_paths.items(), key=lambda x: -x[1])[:40]:
    print("  REACH %d  %s" % (c, p))
print("\ndistinct NON-existent missed paths:", len(unreach_paths))
for p, c in sorted(unreach_paths.items(), key=lambda x: -x[1])[:60]:
    print("  GHOST %d  %s" % (c, p))

out = {"reachable": reachable, "unreachable": unreachable, "no_path": no_path,
       "reach_paths": reach_paths, "unreach_paths": unreach_paths}
Path("_reachability.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
