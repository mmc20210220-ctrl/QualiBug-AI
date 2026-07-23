"""Test BR-PAY-005 reproduction."""
import urllib.request, json, time

BASE = "http://localhost:8000/api/v1"

def api(method, path, body=None, token="acme-admin-token"):
    url = f"{BASE}{path}"
    hdrs = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())

# Get refs
_, depts = api("GET", "/reference/departments")
_, vendors = api("GET", "/reference/vendors")
_, budgets = api("GET", "/budgets")
print(f"Refs: dept={len(depts)}, vendor={len(vendors)}, budget={len(budgets)}")
print(f"Budget available: {budgets[0]['available_amount']}")

# Create contract
cno = f"CF-REPRO-{int(time.time()*1000)%100000}"
st, c = api("POST", "/contracts", {
    "contract_no": cno, "title": "Repro Test", "department_id": depts[0]["id"],
    "vendor_id": vendors[0]["id"], "budget_id": budgets[0]["id"],
    "total_amount": 20000.0, "start_date": "2026-01-01", "end_date": "2026-12-31"
})
print(f"Contract: status={st}, id={c.get('id', '?')[:8]}")
if st != 201:
    print(f"ERROR: {c}")
    exit(1)
cid = c["id"]

# Create milestone
st, m = api("POST", f"/contracts/{cid}/milestones", {"name": "M1", "amount": 20000.0, "due_date": "2026-06-30"})
print(f"Milestone: status={st}")
mid = m["id"]

# Advance contract
st, _ = api("POST", f"/contracts/{cid}/submit", {})
print(f"Submit: {st}")
st, _ = api("POST", f"/contracts/{cid}/legal-approve", {}, token="acme-legal-token")
print(f"Legal: {st}")
st, _ = api("POST", f"/contracts/{cid}/activate", {})
print(f"Activate: {st}")

# Submit + Accept milestone
st, _ = api("POST", f"/milestones/{mid}/submit", {"evidence_url": "https://ev.test/r.pdf"})
print(f"MS Submit: {st}")
st, _ = api("POST", f"/milestones/{mid}/accept", {"accepted_amount": 20000.0})
print(f"MS Accept: {st}")

# Create invoice (total = 10000)
inv_no = f"INV-REPRO-{int(time.time()*1000)%100000}"
st, inv = api("POST", "/invoices", {"contract_id": cid, "invoice_no": inv_no, "subtotal": 9000.0, "tax_amount": 1000.0, "issue_date": "2026-07-01"})
print(f"Invoice: status={st}, total={inv.get('total_amount', '?')}")

# Try payment > invoice total (15000 > 10000)
st2, pr = api("POST", "/payment-requests", {"contract_id": cid, "milestone_id": mid, "invoice_id": inv["id"], "amount": 15000.0})
print(f"\nPayment 15000 > invoice 10000: status={st2}")
print(f"BUG DETECTED (should be 201): {st2 == 201}")
if st2 != 201:
    print(f"Response: {pr}")
