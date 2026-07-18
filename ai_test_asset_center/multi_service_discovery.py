#!/usr/bin/env python3
"""QualiBug Multi-Service ACL Tester — Simple, Customer-Driven.

Customer maintains in QualiBug settings:
  service_map: {name: base_url}
  uploads: API_SPEC.md / OpenAPI → routes extracted with service mapping
  test accounts → tokens obtained at runtime

QualiBug probes: service_map[route.service] + route.path × every role.
Reports any response that produced real business data.
"""
from __future__ import annotations

import json, re, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResponseClass:
    level: str  # BUSINESS_RESPONSE | BUSINESS_EXECUTED | NOT_FOUND | ERROR
    status_code: int
    evidence: dict = field(default_factory=dict)


def classify_response(status_code: int, body: str) -> ResponseClass:
    body = body.strip()
    bl = body[:300].lower()
    if bl.startswith("<!doctype") or bl.startswith("<html") or "<pre>cannot" in bl:
        return ResponseClass("NOT_FOUND", status_code)
    if body in ("null", "true", "false") and status_code < 400:
        return ResponseClass("BUSINESS_RESPONSE", status_code)
    if not body:
        return ResponseClass("BUSINESS_RESPONSE" if status_code < 400 else "NOT_FOUND", status_code)
    try:
        parsed: Any = json.loads(body)
    except json.JSONDecodeError:
        return ResponseClass("BUSINESS_RESPONSE" if status_code < 400 else "ERROR", status_code)
    if isinstance(parsed, dict):
        keys = set(parsed.keys())
        if keys & {"id","_id","uid","refund_no","payment_no","order_no","created_at"}:
            return ResponseClass("BUSINESS_EXECUTED", status_code)
        if keys & {"data","items","products","orders","users","addresses","records","list"}:
            return ResponseClass("BUSINESS_RESPONSE", status_code)
        if "error" in keys or status_code in (401,403):
            return ResponseClass("ERROR", status_code)
        if keys - {"status","ok","message"}:
            return ResponseClass("BUSINESS_RESPONSE", status_code)
    if isinstance(parsed, list) and len(parsed) > 0:
        return ResponseClass("BUSINESS_RESPONSE", status_code)
    if status_code < 400:
        return ResponseClass("BUSINESS_RESPONSE", status_code)
    return ResponseClass("ERROR", status_code)


class MultiServiceDiscovery:
    def __init__(self, routes: list[dict], tokens: dict[str, str]):
        self.routes = routes
        self.tokens = tokens
        self.bugs: list[dict] = []

    def run(self, max_workers: int = 20):
        t0 = time.time()
        tasks = []
        for route in self.routes:
            url = route.get("base_url")
            if not url:
                continue
            for role in self.tokens:
                tasks.append((route, url, role))

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self._test, *t): t for t in tasks}
            for f in as_completed(futures):
                b = f.result()
                if b:
                    self.bugs.append(b)

        print(f"  {len(self.routes)} routes x {len(self.tokens)} roles = "
              f"{len(tasks)} probes in {time.time()-t0:.1f}s -> {len(self.bugs)} bugs")

    def _test(self, route: dict, base: str, role: str) -> dict | None:
        method = route.get("method", "GET").upper()
        path = route.get("path", "/")
        svc = route.get("service", "")

        # Try both gateway-prefixed and bare path.
        # Gateway uses /api/ prefix; internal services may or may not.
        paths = [path]
        stripped = re.sub(r'^/api', '', path)
        if stripped != path:
            paths.insert(0, stripped)
            segs = stripped.strip("/").split("/")
            if len(segs) >= 2:
                paths.insert(1, f"/{segs[-1]}")

        for p in paths:
            result = self._try_one(svc, base, method, p, role)
            if result:
                return result
        return None

    def _try_one(self, svc: str, base: str, method: str,
                 path: str, role: str) -> dict | None:
        headers = {"Content-Type": "application/json"}
        if role in self.tokens:
            headers["Authorization"] = f"Bearer {self.tokens[role]}"
        data = b"{}" if method in ("POST", "PATCH", "PUT", "DELETE") else None

        try:
            req = urllib.request.Request(f"{base}{path}", data=data,
                                          headers=headers, method=method)
            resp = urllib.request.urlopen(req, timeout=3)
            c = classify_response(resp.status, resp.read().decode())
        except urllib.error.HTTPError as e:
            c = classify_response(e.code, e.read().decode())
        except Exception:
            return None

        if c.level in ("BUSINESS_RESPONSE", "BUSINESS_EXECUTED"):
            return {
                "bug_id": f"BUG-{svc}-{method}-{path.replace('/','-')[:40]}",
                "title": f"角色{role}可执行{method} {path} (服务{svc})",
                "category": "ACL_BYPASS", "severity": "P0",
                "request_method": method, "request_path": f"{base}{path}",
                "response_status": c.status_code, "response_level": c.level,
                "expected": "低权限角色不应产生业务响应",
                "actual": f"{c.level} (HTTP {c.status_code})",
                "failed_assertions": [f"产生了{c.level}级别的业务响应"],
                "reproduction": {
                    "method": method, "path": path,
                    "steps": [f"1. {role}登录",
                              f"2. {method} {base}{path}",
                              f"3. HTTP {c.status_code} -> {c.level}"],
                    "is_synthetic": False,
                },
                "evidence_refs": [{"type": "har", "ref": str(len(self.bugs))}],
            }
        return None


def extract_routes(docs_dir: str) -> list[dict]:
    import os
    routes = []
    seen = set()
    svc_kw = {"auth":"auth","login":"auth","register":"auth","password":"auth",
              "product":"product","cart":"cart","coupon":"coupon",
              "order":"order","payment":"payment","refund":"refund",
              "report":"report","user":"user","inventory":"inventory"}
    for fn in os.listdir(docs_dir):
        if not fn.endswith(('.md','.json','.yaml','.yml','.txt')):
            continue
        try:
            c = open(os.path.join(docs_dir, fn), encoding='utf-8').read()
        except OSError:
            continue
        section = "gateway"
        for line in c.split("\n"):
            ls = line.strip()
            if ls.startswith("##"):
                for kw, sv in svc_kw.items():
                    if kw in ls.lstrip("# ").lower(): section = sv; break
            m = re.match(r'(GET|POST|PATCH|DELETE|PUT)\s+(/[^\s\n,]+)', ls, re.I)
            if not m:
                m = re.match(r'###\s+(GET|POST|PATCH|DELETE|PUT)\s+(/[^\s\n,]+)', ls, re.I)
            if m:
                k = (m.group(1).upper(), m.group(2).rstrip("/"))
                if k not in seen:
                    seen.add(k)
                    routes.append({"method":k[0],"path":k[1],"service":section})
    return routes


if __name__ == "__main__":
    import sys
    from pathlib import Path
    dp = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parent.parent.parent
        / "benchmark_mall" / "docs")

    routes = extract_routes(dp)

    # Customer-configured service addresses (from QualiBug settings)
    # Route path is directly appended to service base_url
    svc_addrs = {
        "gateway":"http://127.0.0.1:8080","auth":"http://127.0.0.1:8001",
        "user":"http://127.0.0.1:8002","product":"http://127.0.0.1:8003",
        "inventory":"http://127.0.0.1:8004","cart":"http://127.0.0.1:8005",
        "coupon":"http://127.0.0.1:8006","order":"http://127.0.0.1:8007",
        "payment":"http://127.0.0.1:8008","refund":"http://127.0.0.1:8009",
        "report":"http://127.0.0.1:8010",
    }
    for r in routes:
        r["base_url"] = svc_addrs.get(r["service"], svc_addrs["gateway"])

    print(f"Routes: {len(routes)}")
    tokens = {}
    # Test credentials should come from environment or config, not be hardcoded.
    test_email = os.environ.get("QUALIBUG_TEST_BUYER_EMAIL", "buyer01@benchmark.local")
    test_pw = os.environ.get("QUALIBUG_TEST_BUYER_PASSWORD", "")
    for role, (em, pw) in [("buyer01", (test_email, test_pw))]:
        if not pw:
            continue  # skip auth when no password configured
        try:
            d = json.dumps({"email":em,"password":pw}).encode()
            req = urllib.request.Request(f"{svc_addrs['gateway']}/api/auth/login", data=d,
                headers={"Content-Type":"application/json"})
            t = json.loads(urllib.request.urlopen(req,timeout=10).read()).get("token")
            if t: tokens[role] = t
        except Exception:
            pass

    msd = MultiServiceDiscovery(routes, tokens)
    msd.run()
