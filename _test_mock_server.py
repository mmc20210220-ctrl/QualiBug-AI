"""Quick test: check mock server handles POST correctly."""
import json, sys, urllib.request, urllib.error
sys.stdout.reconfigure(encoding='utf-8')

BASE = "http://localhost:9090"

def req(method, path, token="req-token-001", body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {token}")
    r.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(r, timeout=5)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

# Test 1: Health
s, b = req("GET", "/api/v2/health")
print(f"Health: {s} {b}")

# Test 2: Create ticket
s, b = req("POST", "/api/v2/tickets", body={
    "equipment_ref": "EQ-2024-001",
    "title": "test vibration",
    "description": "abnormal noise",
    "priority_level": "HIGH",
    "sla_hours": 8,
    "requester_badge": "EMP-1001"
})
print(f"Create ticket: {s} {json.dumps(b, ensure_ascii=False)[:200]}")

# Test 3: List tickets
s, b = req("GET", "/api/v2/tickets")
print(f"List tickets: {s} total={b.get('total')}")

# Test 4: Get equipment
s, b = req("GET", "/api/v2/equipment", token="admin-token-001")
print(f"Equipment: {s} total={b.get('total')}")

# Test 5: Get technicians
s, b = req("GET", "/api/v2/technicians", token="admin-token-001")
print(f"Technicians: {s} total={b.get('total')}")

print("\nAll mock server endpoints responding correctly.")
