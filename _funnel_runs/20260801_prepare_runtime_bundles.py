# -*- coding: utf-8 -*-
"""Prepare fresh runtime bundles + refreshed actor tokens for the 2026-08-01 baseline run.

Diagnostic replay preparation only: the benchmark is the explicitly declared
non-production test target at http://localhost:8080, and its own
scripts/init_db_windows.ps1 is the documented governed fixture reset.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen

REPO = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
BENCH_ROOT = Path(
    r"C:\Users\Test\Desktop\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
    r"\qualibug_enterprise_benchmark_v0_5_windows_native_stable"
)
BASE_URL = "http://localhost:8080"
PROJECT_ID = "evaluation-benchmark-mall-held-in-131"

ACCOUNTS = [
    ("buyer01", "buyer", "buyer01@example.com", "Test@123456"),
    ("buyer02", "buyer", "buyer02@example.com", "Test@123456"),
    ("disabled_buyer", "buyer", "disabled_buyer@example.com", "Test@123456"),
    ("seller01", "seller", "seller01@example.com", "Test@123456"),
    ("warehouse01", "warehouse", "warehouse01@example.com", "Test@123456"),
    ("finance01", "finance", "finance01@example.com", "Test@123456"),
    ("auditor01", "auditor", "auditor01@example.com", "Test@123456"),
    ("admin", "admin", "admin@example.com", "Admin@123456"),
]


def login(email: str, password: str) -> dict:
    body = json.dumps({"email": email, "password": password}).encode("utf-8")
    request = Request(
        f"{BASE_URL}/api/auth/login",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("token"):
        raise RuntimeError(f"login returned no token for {email}")
    return payload


def main() -> int:
    rows: list[dict] = []
    for account_ref, role, email, password in ACCOUNTS:
        payload = login(email, password)
        user = payload.get("user") or {}
        rows.append(
            {
                "name": user.get("name") or role,
                "role": user.get("role") or role,
                "email": user.get("email") or email,
                "token": payload["token"],
                "status": user.get("status") or "ACTIVE",
                "account_ref": account_ref,
            }
        )
    accounts = {"accounts": rows}

    # Refresh the product-side catalog used by the runtime (restored after scan).
    catalog_path = REPO / "platform_inputs" / "benchmark_mall" / "test_accounts.json"
    if catalog_path.exists():
        try:
            old = json.loads(catalog_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old = None
        if isinstance(old, dict) and isinstance(old.get("accounts"), list):
            by_email = {row.get("email"): row for row in rows}
            merged = []
            for row in old["accounts"]:
                fresh = by_email.get(row.get("email"))
                merged.append(fresh if fresh is not None else row)
            for row in rows:
                if row.get("email") not in {item.get("email") for item in merged}:
                    merged.append(row)
            accounts = {"accounts": merged}
    catalog_path.write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Mirrored product input for the manifest project identity so the scan's
    # trace ledger and authority fields match the frozen evaluation target.
    project_input = REPO / "platform_inputs" / PROJECT_ID
    project_input.mkdir(parents=True, exist_ok=True)
    source_input = REPO / "platform_inputs" / "benchmark_mall"
    for name in (
        "API_SPEC.md",
        "BUSINESS_RULES.md",
        "DB_SCHEMA.md",
        "DEPLOYMENT.md",
        "HISTORICAL_BUGS.md",
        "PRD.md",
        "TEST_ACCOUNTS.md",
        "USER_ROLES.md",
        "WINDOWS_NATIVE_START.md",
    ):
        (project_input / name).write_text(
            (source_input / name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    (project_input / "real_project_config.json").write_text(
        json.dumps(
            {
                "base_url": BASE_URL,
                "approved_base_url": BASE_URL,
                "environment_type": "test",
                "environment_ref": BASE_URL,
                "target_id": "benchmark-mall-held-in-131",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (project_input / "test_accounts.json").write_text(
        json.dumps(accounts, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    bundle_dir = REPO / "_private_eval" / "benchmark_mall_131_v1" / "runtime" / "held-in-20260801"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    input_bundle = {
        "schema_version": "qualibug.discovery-evaluation-input.v1",
        "project_id": PROJECT_ID,
        "base_url": BASE_URL,
        "api_doc_ref": str(project_input / "API_SPEC.md"),
        "prd_ref": str(project_input / "PRD.md"),
        "multi_layer": True,
    }
    context_bundle = {
        "schema_version": "qualibug.discovery-evaluation-context.v1",
        "campaign_context": {
            "scope_id": "benchmark-mall-held-in-131",
            "environment_ref": BASE_URL,
            "target_environment": "test",
            "execution_mode": "approved_sandbox_write",
            # Sanctioned governed read-only discovery round (planning_round=0):
            # the docs do not document address/user/inventory routes, but the
            # deployment does expose them; confirmed candidates enter Behavior IR
            # only after a correlated active test-actor observation.
            "runtime_interface_discovery_enabled": True,
            "runtime_interface_discovery_budget": 800,
        },
        "test_accounts": accounts,
    }
    fixture_bundle = {
        "schema_version": "qualibug.evaluation-windows-benchmark-fixture.v1",
        "project": PROJECT_ID,
        "base_url": BASE_URL,
        "target_root": str(BENCH_ROOT),
    }
    (bundle_dir / "input.json").write_text(
        json.dumps(input_bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (bundle_dir / "context.json").write_text(
        json.dumps(context_bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (bundle_dir / "fixture.json").write_text(
        json.dumps(fixture_bundle, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(
        {
            "refreshed_accounts": len(rows),
            "catalog": str(catalog_path),
            "bundle_dir": str(bundle_dir),
            "token_expiry_ts": int(time.time()) + 8 * 3600,
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
