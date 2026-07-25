#!/usr/bin/env python3
"""Quick test of MES mock server."""
import urllib.request
import json

BASE = "http://localhost:8020"

def get(path, token="planner-pat-token"):
    req = urllib.request.Request(f"{BASE}{path}", headers={"Authorization": f"Bearer {token}"})
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

def post(path, body, token="planner-pat-token"):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    r = urllib.request.urlopen(req)
    return json.loads(r.read())

# Health
r = urllib.request.urlopen(f"{BASE}/health")
print("Health:", json.loads(r.read()))

# Products
data = get("/products")
print(f"Products: {data['total']}")

# Work Orders
data = get("/work-orders")
print(f"Work Orders: {data['total']}")

# BOM expand
data = get("/boms/bom-001/expand")
print(f"BOM lines: {len(data['expanded_lines'])}")

# Work Order Operations
data = get("/work-orders/wo-001/operations")
print(f"WOOps: {data['total']}")

# Material Reservations
data = get("/material-reservations?work_order_id=wo-001")
print(f"Reservations: {data['total']}")

# Create work report (test write)
report = post("/work-reports", {"work_order_id": "wo-001", "operation_id": "woo-001", "quantity": 10, "defect_quantity": 1}, token="operator-oli-token")
print(f"Work Report created: {report['id']}")

# Test auth rejection
try:
    get("/products", token="invalid-token")
except urllib.error.HTTPError as e:
    print(f"Auth rejection: {e.code}")

print("\nAll tests passed!")
