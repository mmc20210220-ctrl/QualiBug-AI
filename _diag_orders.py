"""Diagnose why /api/orders count went UP from 78 to 82."""
import urllib.request, json, http.cookiejar, urllib.parse
from collections import Counter

BASE = "http://127.0.0.1:8088"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

# Login
body = json.dumps({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request(f"{BASE}/api/auth/login", data=body,
                             headers={"Content-Type": "application/json"})
resp = opener.open(req)
token = json.loads(resp.read()).get("token", "")

# Get command-center
pid = urllib.parse.quote("第一个真实项目测试")
req2 = urllib.request.Request(f"{BASE}/api/v1/projects/{pid}/command-center",
                              headers={"Authorization": f"Bearer {token}"})
resp2 = opener.open(req2)
payload = json.loads(resp2.read())
risks = payload.get("data", {}).get("risks", [])

# Filter /api/orders findings
orders = [r for r in risks if r.get("repro_path") == "/api/orders"]
print(f"Total risks: {len(risks)}")
print(f"/api/orders findings: {len(orders)}")

# Check which are from cumulative (DB) vs current report
cumulative = [r for r in orders if r.get("_cumulative")]
current = [r for r in orders if not r.get("_cumulative")]
print(f"  From current scan_result.json: {len(current)}")
print(f"  From DB cumulative (_cumulative=True): {len(cumulative)}")

# Show cumulative ones
if cumulative:
    print("\n=== Cumulative findings added on top ===")
    for r in cumulative:
        print(f"  [{r.get('severity')}] {r.get('title','')[:80]}")

# Check for findings with same title (real duplicates)
title_counts = Counter(r.get("title","")[:80] for r in orders)
dupes = [(t,c) for t,c in title_counts.items() if c > 1]
if dupes:
    print(f"\n=== Duplicate titles in /api/orders ({len(dupes)}) ===")
    for t, c in sorted(dupes, key=lambda x: -x[1])[:10]:
        print(f"  ({c}x) {t}")
else:
    print("\nNo duplicate titles in /api/orders")

# Check DB count
import sqlite3
conn = sqlite3.connect("qualibug.db")
conn.row_factory = sqlite3.Row
db_count = conn.execute("SELECT COUNT(*) FROM findings WHERE project_id='第一个真实项目测试' AND status='open'").fetchone()[0]
print(f"\nDB open findings: {db_count}")

# Check scan_result.json finding count
import os
scan_path = "platform_outputs/第一个真实项目测试/scan_result.json"
if os.path.exists(scan_path):
    data = json.loads(open(scan_path, encoding="utf-8").read())
    real_findings = data.get("real_findings") or data.get("bug_scores") or []
    print(f"scan_result.json real_findings: {len(real_findings)}")
    # Count paths in scan_result
    path_counts = Counter()
    for f in real_findings:
        if isinstance(f, dict):
            p = f.get("path") or f.get("_api_path") or ""
            ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
            if not p:
                p = ev.get("path", "")
            path_counts[p or "(empty)"] += 1
    print("\n=== Paths in scan_result.json ===")
    for p, c in path_counts.most_common(10):
        print(f"  {c:3d}  {p}")

conn.close()
