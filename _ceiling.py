#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Definitive recall-ceiling analysis (offline diagnosis only).

For each of the 131 GT bugs, extract the endpoint(s) referenced in its
trigger/expected/actual text and check whether the REAL benchmark_mall target
(gateway:8080 route table) actually serves them. Bugs whose endpoints are not
served can never be triggered -> structurally unreachable on this target.
"""
import json, re
from pathlib import Path

routes = json.loads(Path("_routes_desktop.json").read_text(encoding="utf-8"))
prefix_by_svc = {
    "auth-service": "/api/auth", "user-service": "/api/users",
    "product-service": "/api/products", "inventory-service": "/api/inventory",
    "cart-service": "/api/cart", "coupon-service": "/api/coupons",
    "order-service": "/api/orders", "payment-service": "/api/payments",
    "refund-service": "/api/refunds", "report-service": "/api/reports",
}
full = []  # (method, regex, fullpath)
for svc, rs in routes.items():
    pre = prefix_by_svc.get(svc)
    if not pre:
        continue
    for r in rs:
        p = r["p"]
        if p == "/health":
            continue
        fp = (pre + p) if p != "/" else pre
        rx = re.sub(r":[A-Za-z0-9_]+", "[^/]+", re.escape(fp))
        full.append((r["m"], re.compile("^" + rx + "$"), fp))

def served(path):
    """Return list of (method, route) that serve this path (pattern-level)."""
    path = str(path).split("?")[0].rstrip("/")
    return [(m, fp) for m, rx, fp in full if rx.match(path)]

bugs = json.loads(Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json").read_text(encoding="utf-8"))
print("GT bugs:", len(bugs))

path_rx = re.compile(r"/api/[A-Za-z0-9_\-/:.\*]+")
reachable, ghost, no_path = [], [], []
for b in bugs:
    text = " ".join(str(b.get(k, "")) for k in ("trigger", "expected", "actual", "title"))
    paths = sorted(set(path_rx.findall(text)))
    # normalize :param and * to a probe-able concrete segment for matching
    hits = []
    for p in paths:
        probe = re.sub(r"[:\*][A-Za-z0-9_]*", "x1", p)  # :id / * -> dummy id
        probe = re.sub(r"\*", "x1", probe)
        if served(probe) or served(p):
            hits.append(p)
    if not paths:
        no_path.append(b)
    elif hits:
        reachable.append((b, paths, hits))
    else:
        ghost.append((b, paths))

print("\nREACHABLE (endpoint served by real target):", len(reachable))
print("GHOST (endpoint NOT served by real target):", len(ghost))
print("NO explicit /api path in trigger:", len(no_path))

print("\n--- by module ---")
from collections import Counter
for label, items in (("REACH", [x[0] for x in reachable]), ("GHOST", [x[0] for x in ghost]), ("NOPATH", no_path)):
    c = Counter(str(b.get("module")) for b in items)
    print(f"  {label:6s}: {dict(sorted(c.items()))}")

print("\n--- GHOST bugs (endpoint, bug_id, module) ---")
for b, paths in ghost:
    print(f"  {b['bug_id']:12s} {str(b.get('module')):18s} {','.join(paths)}")

print("\n--- NO-PATH bugs (bug_id, module, title) ---")
for b in no_path:
    print(f"  {b['bug_id']:12s} {str(b.get('module')):18s} {str(b.get('title'))[:40]}")

out = {
    "total": len(bugs),
    "reachable": [{"bug_id": b["bug_id"], "module": b.get("module"), "paths": p, "hits": h} for b, p, h in reachable],
    "ghost": [{"bug_id": b["bug_id"], "module": b.get("module"), "paths": p} for b, p in ghost],
    "no_path": [{"bug_id": b["bug_id"], "module": b.get("module")} for b in no_path],
}
Path("_ceiling.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print("\nsaved _ceiling.json")
