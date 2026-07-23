"""P0-10: Reproduce findings from Project B scan."""
import requests
import json

BASE = "http://localhost:9092"
H_ADMIN = {"Authorization": "Bearer admin-token-001"}
H_TECH = {"Authorization": "Bearer tech-token-001"}
H_REQ = {"Authorization": "Bearer req-token-001"}

print("=" * 60)
print("P0-10: Finding Reproduction Verification")
print("=" * 60)

# Finding 1: GET /api/v2/tickets/:ref/settlement returns 404 for non-existent settlement
print("\n[Finding 1] GET settlement for ticket without settlement")
# First create a ticket
r = requests.post(f"{BASE}/api/v2/tickets", headers=H_REQ, json={
    "equipment_ref": "EQ-2024-001",
    "title": "Test ticket for settlement",
    "priority_level": "NORMAL"
})
ticket = r.json()
ticket_ref = ticket.get("ticket_ref")
print(f"  Created ticket: {ticket_ref} (status={ticket.get('ticket_status')})")

# Try to get settlement (should be 404 - no settlement exists)
r = requests.get(f"{BASE}/api/v2/tickets/{ticket_ref}/settlement", headers=H_ADMIN)
print(f"  GET settlement: {r.status_code} -> {r.json().get('error_code', 'OK')}")
finding1_reproduced = r.status_code == 404
print(f"  REPRODUCED: {finding1_reproduced} (expected 404 for missing settlement)")

# Finding 2: Technician PATCH - authorization issue
print("\n[Finding 2] PATCH technician by admin (authorization test)")
r = requests.patch(f"{BASE}/api/v2/technicians/TECH-2001", headers=H_ADMIN, json={
    "technician_status": "AVAILABLE"
})
print(f"  Admin PATCH technician: {r.status_code}")
finding2_reproduced = r.status_code == 200  # Admin can modify technician
print(f"  Result: {r.status_code} (admin can modify technician status)")

# Finding 3: Technician self-modification
print("\n[Finding 3] PATCH technician by technician (self-modification)")
r = requests.patch(f"{BASE}/api/v2/technicians/TECH-2001", headers=H_TECH, json={
    "technician_status": "ON_LEAVE"
})
print(f"  Technician PATCH self: {r.status_code}")
finding3_reproduced = r.status_code == 200  # Technician can modify self
print(f"  Result: {r.status_code} (technician can modify own status)")

# Additional verification: State transition bug detection
print("\n[Additional] State transition verification")
# Create a new ticket and try invalid state transition
r = requests.post(f"{BASE}/api/v2/tickets", headers=H_REQ, json={
    "equipment_ref": "EQ-2024-002",
    "title": "State transition test",
    "priority_level": "NORMAL"
})
t2 = r.json()
t2_ref = t2.get("ticket_ref")
print(f"  Created ticket: {t2_ref} (status=DRAFT)")

# Try to assign without submitting (invalid: DRAFT -> ASSIGNED)
r = requests.post(f"{BASE}/api/v2/tickets/{t2_ref}/assign", headers=H_ADMIN, json={
    "technician_badge": "TECH-2001"
})
print(f"  Assign without submit: {r.status_code} -> {r.json().get('error_code', 'OK')}")
state_transition_detected = r.status_code == 400
print(f"  State machine enforced: {state_transition_detected}")

# Summary
print("\n" + "=" * 60)
print("REPRODUCTION SUMMARY")
print("=" * 60)
print(f"  Finding 1 (settlement 404): {'REPRODUCED' if finding1_reproduced else 'NOT REPRODUCED'}")
print(f"  Finding 2 (admin PATCH): {'REPRODUCED' if finding2_reproduced else 'NOT REPRODUCED'}")
print(f"  Finding 3 (tech self-PATCH): {'REPRODUCED' if finding3_reproduced else 'NOT REPRODUCED'}")
print(f"  State machine enforcement: {'VERIFIED' if state_transition_detected else 'NOT VERIFIED'}")
print(f"\n  P0-10 PASS: {finding1_reproduced and state_transition_detected}")
