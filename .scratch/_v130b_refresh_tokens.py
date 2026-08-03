"""V1.3.0-B Phase 0: Refresh benchmark test account tokens.

Calls the live benchmark target login endpoint to obtain fresh JWTs,
then updates platform_inputs test_accounts.json files.
"""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE_URL = "http://localhost:8080"

# Source of truth: projects/benchmark_mall/input/TEST_ACCOUNTS.md
ACCOUNTS = [
    {"name": "buyer01", "role": "buyer", "email": "buyer01@example.com", "password": "Test@123456"},
    {"name": "buyer02", "role": "buyer", "email": "buyer02@example.com", "password": "Test@123456"},
    {"name": "seller01", "role": "seller", "email": "seller01@example.com", "password": "Test@123456"},
    {"name": "warehouse01", "role": "warehouse", "email": "warehouse01@example.com", "password": "Test@123456"},
    {"name": "finance01", "role": "finance", "email": "finance01@example.com", "password": "Test@123456"},
    {"name": "auditor01", "role": "auditor", "email": "auditor01@example.com", "password": "Test@123456"},
    {"name": "admin", "role": "admin", "email": "admin@example.com", "password": "Admin@123456"},
]

TARGET_FILES = [
    ROOT / "platform_inputs" / "benchmark_mall_131" / "test_accounts.json",
    ROOT / "platform_inputs" / "benchmark_mall" / "test_accounts.json",
]


def login(email: str, password: str) -> str | None:
    """Call POST /api/auth/login and return the JWT token or None."""
    data = json.dumps({"email": email, "password": password}).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/api/auth/login",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = json.loads(resp.read())
            return body.get("token")
    except urllib.error.HTTPError as exc:
        print(f"  [FAIL] {email}: HTTP {exc.code}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  [FAIL] {email}: {exc}", file=sys.stderr)
        return None


def verify_token(token: str, label: str) -> bool:
    """Quick verification: GET /api/users/me with the token."""
    req = urllib.request.Request(
        f"{BASE_URL}/api/users/me",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except urllib.error.HTTPError:
        # Some targets may not have /users/me; try /api/products as fallback
        try:
            req2 = urllib.request.Request(
                f"{BASE_URL}/api/products",
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
            with urllib.request.urlopen(req2, timeout=10) as resp2:
                return resp2.status == 200
        except Exception:
            return False
    except Exception:
        return False


def main() -> None:
    print(f"[V1.3.0-B] Refreshing tokens from {BASE_URL}...")
    refreshed: list[dict] = []
    failed: list[str] = []

    for acct in ACCOUNTS:
        token = login(acct["email"], acct["password"])
        if token:
            refreshed.append({
                "name": acct["name"],
                "role": acct["role"],
                "email": acct["email"],
                "token": token,
                "status": "active",
                "account_ref": acct["name"],
            })
            print(f"  [OK] {acct['name']} ({acct['role']})")
        else:
            failed.append(acct["name"])
            # Keep a placeholder entry without token
            refreshed.append({
                "name": acct["name"],
                "role": acct["role"],
                "email": acct["email"],
                "token": "",
                "status": "login_failed",
                "account_ref": acct["name"],
            })

    if failed:
        print(f"\n[WARN] Failed logins: {failed}", file=sys.stderr)

    # Verify at least one token works
    active_tokens = [r for r in refreshed if r["token"]]
    if active_tokens:
        sample = active_tokens[0]
        ok = verify_token(sample["token"], sample["name"])
        print(f"\n[VERIFY] Token for {sample['name']}: {'VALID' if ok else 'INVALID (but login succeeded)'}")

    # Write to target files
    payload = {"accounts": refreshed}
    for target in TARGET_FILES:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"[WRITE] {target.relative_to(ROOT)}")

    ok_count = sum(1 for r in refreshed if r["token"])
    print(f"\n[DONE] {ok_count}/{len(ACCOUNTS)} tokens refreshed successfully.")
    if ok_count < len(ACCOUNTS):
        print("[WARN] Some accounts failed to login. Check target availability.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
