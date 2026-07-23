"""Fetch ContractFlow OpenAPI spec and test login."""
import requests
import json
from pathlib import Path

# 1. Get OpenAPI spec
r = requests.get("http://localhost:8000/openapi.json", timeout=5)
spec = r.json()
Path("_contractflow_openapi.json").write_text(
    json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8"
)
paths = spec.get("paths", {})
schemas = spec.get("components", {}).get("schemas", {})
print(f"OpenAPI spec saved: {len(paths)} paths, {len(schemas)} schemas")

# 2. Check login schema
login_schema = schemas.get("LoginRequest", {})
print(f"\nLoginRequest schema: {json.dumps(login_schema, indent=2)}")

# 3. Try login with common test accounts
test_accounts = [
    {"email": "admin@contractflow.com", "password": "admin123"},
    {"email": "admin@example.com", "password": "admin123"},
    {"email": "test@example.com", "password": "test123"},
    {"email": "alice@example.com", "password": "123456"},
    {"username": "admin", "password": "admin123"},
    {"username": "admin", "password": "admin"},
]

print("\n=== Testing login ===")
for account in test_accounts:
    try:
        r = requests.post("http://localhost:8000/api/v1/auth/login", json=account, timeout=5)
        if r.status_code == 200:
            data = r.json()
            print(f"  SUCCESS: {account} -> token={str(data.get('token',''))[:20]}...")
            print(f"    user: {data.get('user', {})}")
        else:
            print(f"  FAIL {r.status_code}: {account}")
    except Exception as e:
        print(f"  ERROR: {account} -> {e}")

# 4. List all paths with methods
print("\n=== All API paths ===")
for p in sorted(paths.keys()):
    methods = [m.upper() for m in paths[p].keys() if m in ("get", "post", "put", "patch", "delete")]
    print(f"  {' '.join(methods):20s} {p}")

# 5. Check cleanup-relevant endpoints
print("\n=== Cleanup/Cancel endpoints ===")
for p in sorted(paths.keys()):
    if any(kw in p.lower() for kw in ["cancel", "delete", "reject", "close", "void"]):
        methods = [m.upper() for m in paths[p].keys() if m in ("get", "post", "put", "patch", "delete")]
        print(f"  {' '.join(methods):20s} {p}")
