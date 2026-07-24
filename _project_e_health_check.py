"""Project E Phase 3: Environment Health Check."""
import json
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).parent
BASE_URL = "http://localhost:8003"

def api(method, path, token=None, body=None):
    """Make API request."""
    url = BASE_URL + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body.decode()) if body else {}
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": str(e)}

print("=" * 70)
print("  PROJECT E - PHASE 3: ENVIRONMENT HEALTH CHECK")
print("=" * 70)

now = datetime.now(timezone.utc).isoformat()
checks = []

# ─── 1. Health endpoint ───
print("\n[1/4] Health Endpoint")
status, data = api("GET", "/health")
health_ok = status == 200 and data.get("status") == "healthy"
checks.append({"check": "health_endpoint", "status": status, "pass": health_ok})
print(f"  GET /health: {status} - {'PASS' if health_ok else 'FAIL'}")

# ─── 2. Account Authentication ───
print("\n[2/4] Account Authentication")
accounts = [
    ("operator-omar-token", "OPERATOR", "acme"),
    ("operator-olga-token", "OPERATOR", "acme"),
    ("manager-mia-token", "MANAGER", "acme"),
    ("ordermgr-nina-token", "ORDER_MANAGER", "acme"),
    ("customer-cara-token", "CUSTOMER", "acme"),
    ("admin-alex-token", "ADMIN", "acme"),
    ("auditor-ava-token", "AUDITOR", "acme"),
    ("operator-oscar-token", "OPERATOR", "globex"),
    ("manager-max-token", "MANAGER", "globex"),
    ("ordermgr-noah-token", "ORDER_MANAGER", "globex"),
    ("customer-carl-token", "CUSTOMER", "globex"),
    ("admin-anna-token", "ADMIN", "globex"),
]

auth_pass = 0
auth_fail = 0
for token, role, org in accounts:
    status, data = api("GET", "/warehouses", token=token)
    ok = status == 200
    if ok:
        auth_pass += 1
    else:
        auth_fail += 1
    print(f"  {token[:20]:20} ({role:14} {org:6}): {'PASS' if ok else 'FAIL'}")

checks.append({"check": "account_authentication", "total": len(accounts), "pass_count": auth_pass, "fail_count": auth_fail, "pass": auth_fail == 0})

# ─── 3. Basic Fixture Access ───
print("\n[3/4] Basic Fixture Access")
fixtures = [
    ("GET", "/warehouses", "admin-alex-token", "warehouses"),
    ("GET", "/products", "admin-alex-token", "products"),
    ("GET", "/batches", "admin-alex-token", "batches"),
    ("GET", "/orders", "admin-alex-token", "orders"),
    ("GET", "/suppliers", "admin-alex-token", "suppliers"),
]

fixture_pass = 0
fixture_fail = 0
for method, path, token, name in fixtures:
    status, data = api(method, path, token=token)
    ok = status == 200
    count = len(data) if isinstance(data, list) else data.get("total", 0)
    if ok:
        fixture_pass += 1
    else:
        fixture_fail += 1
    print(f"  {method} {path:20}: {status} (count={count}) - {'PASS' if ok else 'FAIL'}")

checks.append({"check": "fixture_access", "total": len(fixtures), "pass_count": fixture_pass, "fail_count": fixture_fail, "pass": fixture_fail == 0})

# ─── 4. Write Operation Test ───
print("\n[4/4] Write Operation Test (non-destructive)")
# Create a test product (will be in ACME org)
status, data = api("POST", "/products", token="admin-alex-token", body={
    "sku": "SKU-TEST-HEALTH",
    "name": "Health Check Test Product",
    "unit_price": 1.00,
    "weight_kg": 0.1,
    "category": "GENERAL"
})
write_ok = status in (200, 201)
print(f"  POST /products (test): {status} - {'PASS' if write_ok else 'FAIL'}")
checks.append({"check": "write_operation", "status": status, "pass": write_ok})

# ─── Summary ───
all_pass = all(c["pass"] for c in checks)
print("\n" + "=" * 70)
print(f"  ENVIRONMENT HEALTH: {'READY' if all_pass else 'NOT READY'}")
print("=" * 70)

# ─── Output JSON ───
readiness = {
    "environment_readiness_id": "project_e_environment_readiness_v1",
    "created_at": now,
    "project_id": "warehouse_e",
    "base_url": BASE_URL,
    "mock_server": "projects/warehouse_e/mock_server.py",
    "port": 8003,
    "checks": checks,
    "summary": {
        "health_endpoint": checks[0]["pass"],
        "accounts_verified": auth_pass,
        "accounts_total": len(accounts),
        "fixtures_accessible": fixture_pass,
        "fixtures_total": len(fixtures),
        "write_operations": write_ok
    },
    "overall_ready": all_pass,
    "bug_probing_executed": False,
    "note": "Health check only - no bug detection attempted"
}

out_path = ROOT / "project_e_environment_readiness.json"
out_path.write_text(json.dumps(readiness, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n  Output: {out_path.name}")
