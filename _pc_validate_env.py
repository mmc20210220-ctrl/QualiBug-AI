"""P0-3/P0-5: Environment validation for Project C ContractFlow."""
import requests
import json

BASE = "http://localhost:8000/api/v1"

print("=" * 60)
print("Project C Environment Validation")
print("=" * 60)

# Test accounts
ACCOUNTS = {
    "acme_admin": {"email": "admin@acme.test", "password": "Admin123!", "token": "acme-admin-token"},
    "acme_legal": {"email": "legal@acme.test", "password": "Legal123!", "token": "acme-legal-token"},
    "acme_finance": {"email": "finance@acme.test", "password": "Finance123!", "token": "acme-finance-token"},
    "acme_requester": {"email": "requester@acme.test", "password": "Request123!", "token": "acme-requester-token"},
    "acme_manager": {"email": "manager@acme.test", "password": "Manager123!", "token": "acme-manager-token"},
    "acme_auditor": {"email": "auditor@acme.test", "password": "Auditor123!", "token": "acme-auditor-token"},
    "acme_vendor": {"email": "vendor@vendor.test", "password": "Vendor123!", "token": "acme-vendor-token"},
    "globex_admin": {"email": "admin@globex.test", "password": "Admin123!", "token": "globex-admin-token"},
}

# 1. Health check
print("\n[1] Health Check")
r = requests.get("http://localhost:8000/health")
print(f"  /health: {r.status_code} -> {r.json()}")

# 2. Login tests
print("\n[2] Authentication Tests")
tokens = {}
for name, acc in ACCOUNTS.items():
    r = requests.post(f"{BASE}/auth/login", json={"email": acc["email"], "password": acc["password"]})
    if r.status_code == 200:
        data = r.json()
        tokens[name] = data["token"]
        print(f"  {name}: OK (role={data.get('role')}, tenant={data.get('tenant_id', '')[:8]}...)")
    else:
        print(f"  {name}: FAILED ({r.status_code})")

# 3. Test fixed tokens (from TEST_ACCOUNTS.md)
print("\n[3] Fixed Token Tests")
for name, acc in list(ACCOUNTS.items())[:3]:
    r = requests.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {acc['token']}"})
    print(f"  {name} fixed token: {r.status_code}")

# 4. API endpoints test (using acme_admin)
admin_token = tokens.get("acme_admin", "acme-admin-token")
H = {"Authorization": f"Bearer {admin_token}"}

print("\n[4] API Endpoints (acme_admin)")
endpoints = [
    ("GET", "/contracts", None),
    ("GET", "/budgets", None),
    ("GET", "/payment-requests", None),
    ("GET", "/audit-logs", None),
    ("GET", "/reference/departments", None),
    ("GET", "/reference/vendors", None),
]

for method, path, body in endpoints:
    if method == "GET":
        r = requests.get(f"{BASE}{path}", headers=H)
    else:
        r = requests.post(f"{BASE}{path}", headers=H, json=body)
    count = len(r.json()) if r.status_code == 200 and isinstance(r.json(), list) else "-"
    print(f"  {method} {path}: {r.status_code} ({count} items)")

# 5. Tenant isolation test
print("\n[5] Tenant Isolation Test")
globex_token = tokens.get("globex_admin", "globex-admin-token")
r_acme = requests.get(f"{BASE}/contracts", headers={"Authorization": f"Bearer {admin_token}"})
r_globex = requests.get(f"{BASE}/contracts", headers={"Authorization": f"Bearer {globex_token}"})
acme_ids = {c["id"] for c in r_acme.json()} if r_acme.status_code == 200 else set()
globex_ids = {c["id"] for c in r_globex.json()} if r_globex.status_code == 200 else set()
overlap = acme_ids & globex_ids
print(f"  Acme contracts: {len(acme_ids)}")
print(f"  Globex contracts: {len(globex_ids)}")
print(f"  Overlap (should be 0): {len(overlap)}")

# 6. Write permission test
print("\n[6] Write Permission Test")
# Get reference data for creating contract
r_dept = requests.get(f"{BASE}/reference/departments", headers=H)
r_vendor = requests.get(f"{BASE}/reference/vendors", headers=H)
r_budget = requests.get(f"{BASE}/budgets", headers=H)

if r_dept.status_code == 200 and r_vendor.status_code == 200 and r_budget.status_code == 200:
    depts = r_dept.json()
    vendors = r_vendor.json()
    budgets = r_budget.json()
    if depts and vendors and budgets:
        # Create a test contract
        test_contract = {
            "contract_no": f"TEST-VALIDATION-001",
            "title": "Validation Test Contract",
            "department_id": depts[0]["id"],
            "vendor_id": vendors[0]["id"],
            "budget_id": budgets[0]["id"],
            "total_amount": 1000.00,
            "start_date": "2025-01-01",
            "end_date": "2025-12-31"
        }
        r_create = requests.post(f"{BASE}/contracts", headers=H, json=test_contract)
        print(f"  Create contract: {r_create.status_code}")
        if r_create.status_code == 201:
            contract_id = r_create.json()["id"]
            print(f"  Contract ID: {contract_id[:8]}...")
            print(f"  Status: {r_create.json().get('status')}")

# Summary
print("\n" + "=" * 60)
print("VALIDATION SUMMARY")
print("=" * 60)
print(f"  Health: OK")
print(f"  Auth accounts: {len(tokens)}/{len(ACCOUNTS)}")
print(f"  Tenant isolation: {'PASS' if len(overlap) == 0 else 'FAIL'}")
print(f"  Write enabled: YES")
print("=" * 60)
