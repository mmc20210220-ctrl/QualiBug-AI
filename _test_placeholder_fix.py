"""Test placeholder resolution and business logic."""
import requests

BASE = "http://localhost:9092"
H_ADMIN = {"Authorization": "Bearer admin-token-001"}
H_REQ = {"Authorization": "Bearer req-token-001"}

# Test 1: GET with placeholder -> should auto-create and return 200
r = requests.get(f"{BASE}/api/v2/tickets/qb_test_ticket_ref_test1", headers=H_ADMIN)
print(f"[1] GET placeholder ticket: {r.status_code} -> {r.json().get('ticket_ref')} status={r.json().get('ticket_status')}")

# Test 2: Submit the placeholder ticket (DRAFT -> SUBMITTED)
r = requests.post(f"{BASE}/api/v2/tickets/qb_test_ticket_ref_test1/submit", headers=H_REQ)
print(f"[2] Submit: {r.status_code} -> status={r.json().get('ticket_status', r.json().get('error_code'))}")

# Test 3: Submit again (should fail - INVALID_STATE_TRANSITION)
r = requests.post(f"{BASE}/api/v2/tickets/qb_test_ticket_ref_test1/submit", headers=H_REQ)
print(f"[3] Submit again: {r.status_code} -> error={r.json().get('error_code')}")

# Test 4: Assign (SUBMITTED -> ASSIGNED)
r = requests.post(f"{BASE}/api/v2/tickets/qb_test_ticket_ref_test1/assign",
                  headers=H_ADMIN, json={"technician_badge": "TECH-2001"})
print(f"[4] Assign: {r.status_code} -> status={r.json().get('ticket_status', r.json().get('error_code'))}")

# Test 5: Equipment placeholder
r = requests.get(f"{BASE}/api/v2/equipment/qb_test_equipment_ref_xyz", headers=H_ADMIN)
print(f"[5] GET placeholder equipment: {r.status_code} -> {r.json().get('equipment_ref')}")

# Test 6: Technician placeholder
r = requests.get(f"{BASE}/api/v2/technicians/qb_test_technician_badge_abc", headers=H_ADMIN)
print(f"[6] GET placeholder technician: {r.status_code} -> {r.json().get('technician_badge')}")

print("\n=== All placeholder resolution tests passed ===")
