"""Trace where /api/orders path comes from for pathless findings."""
import urllib.request, json, http.cookiejar, urllib.parse

BASE = "http://127.0.0.1:8088"
cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

body = json.dumps({"username": "admin", "password": "admin123"}).encode()
req = urllib.request.Request(f"{BASE}/api/auth/login", data=body,
                             headers={"Content-Type": "application/json"})
resp = opener.open(req)
token = json.loads(resp.read()).get("token", "")

pid = urllib.parse.quote("第一个真实项目测试")
req2 = urllib.request.Request(f"{BASE}/api/v1/projects/{pid}/command-center",
                              headers={"Authorization": f"Bearer {token}"})
resp2 = opener.open(req2)
payload = json.loads(resp2.read())
risks = payload.get("data", {}).get("risks", [])

# Find findings with repro_path=/api/orders and check ALL path-related fields
orders = [r for r in risks if r.get("repro_path") == "/api/orders"]
print(f"Findings with repro_path=/api/orders: {len(orders)}")

# Show first 3 with all path-related fields
for i, r in enumerate(orders[:3]):
    print(f"\n=== Finding {i} ===")
    print(f"  title: {r.get('title','')[:80]}")
    print(f"  repro_path: '{r.get('repro_path','')}'")
    print(f"  repro_method: '{r.get('repro_method','')}'")
    print(f"  _api_path: '{r.get('_api_path','')}'")
    print(f"  _api_method: '{r.get('_api_method','')}'")
    ev = r.get("evidence") or {}
    print(f"  evidence.path: '{ev.get('path','')}'")
    print(f"  evidence.method: '{ev.get('method','')}'")
    print(f"  path: '{r.get('path','')}'")
    print(f"  source_entity: '{r.get('source_entity','')}'")
    print(f"  source_value: '{r.get('source_value','')}'")
    print(f"  risk_type: '{r.get('risk_type','')}'")
    print(f"  category: '{r.get('category','')}'")

# Check findings with EMPTY repro_path
empty = [r for r in risks if not r.get("repro_path")]
print(f"\n\nFindings with empty repro_path: {len(empty)}")
for r in empty[:3]:
    print(f"  title: {r.get('title','')[:80]}")
    print(f"  _api_path: '{r.get('_api_path','')}'")
    print(f"  evidence.path: '{(r.get('evidence') or {}).get('path','')}'")
