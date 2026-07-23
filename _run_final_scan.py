"""Execute final scan for contractflow_project_c with correct configuration."""
import requests
import json
import time
import sys

BASE = "http://localhost:8088"
PROJECT = "contractflow_project_c"

# Source manifest from registry
SOURCE_ID = "src_68e5e273aaf8f71e"
SOURCE_HASH = "27258d4ff23506c482097a8dc447ef77ddd819d0e911f92400212eecea32cd1c"

scan_body = {
    "project": PROJECT,
    "base_url": "http://localhost:8000",
    "scope_id": "contractflow-local",
    "environment_ref": "sandbox",
    "environment_type": "test",
    "execution_mode": "approved_sandbox_write",
    "source_manifest": {
        "source_id": SOURCE_ID,
        "source_hash": SOURCE_HASH,
    },
    "test_data_contract": {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "contractflow-local",
    },
    "campaign_context": {
        "campaign_id": f"PROJECT_C_POST_TUNING_ORACLE_V1_FINAL_{int(time.time())}",
        "scope_id": "contractflow-local",
        "environment_ref": "sandbox",
        "environment_type": "test",
        "mainline_authority": "experiment_candidate",
    },
}

print(f"Starting scan for project: {PROJECT}")
print(f"Target: http://localhost:8000")
print(f"Environment: sandbox (test)")
print(f"Strategy: create_disposable")
print(f"Campaign: {scan_body['campaign_context']['campaign_id']}")
print()

start = time.time()
try:
    r = requests.post(
        f"{BASE}/api/v1/scan",
        json=scan_body,
        timeout=3600,  # 1 hour timeout
        headers={"Content-Type": "application/json"},
    )
    elapsed = time.time() - start
    print(f"Response status: {r.status_code} ({elapsed:.1f}s)")
    
    if r.status_code == 200:
        result = r.json()
        # Save result
        with open("_scan_result_final.json", "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Result saved to _scan_result_final.json")
        print(f"  success: {result.get('success')}")
        print(f"  total_findings: {result.get('total_findings')}")
        print(f"  total_candidates: {result.get('total_candidates')}")
        
        # Check test_data_bootstrap
        bootstrap = result.get("test_data_bootstrap", {})
        print(f"\n  test_data_bootstrap:")
        print(f"    status: {bootstrap.get('status')}")
        print(f"    reason: {bootstrap.get('reason')}")
        if bootstrap.get("creation_receipt"):
            print(f"    creation_receipt: {bootstrap['creation_receipt'].get('receipt_id')}")
        if bootstrap.get("cleanup_receipt"):
            print(f"    cleanup_receipt: {bootstrap['cleanup_receipt'].get('receipt_id')}")
        
        # Check test_data_plan
        plan = result.get("test_data_plan", {})
        print(f"\n  test_data_plan:")
        print(f"    status: {plan.get('status')}")
        print(f"    missing: {plan.get('missing_requirements')}")
        
        # Check coverage gaps
        gaps = result.get("coverage_gaps", [])
        tdg = [g for g in gaps if g.get("kind") == "TEST_DATA_GAP"]
        print(f"\n  TEST_DATA_GAP count: {len(tdg)}")
        for g in tdg[:3]:
            print(f"    {g.get('code')}")
    else:
        print(f"Error: {r.text[:500]}")
except requests.exceptions.Timeout:
    elapsed = time.time() - start
    print(f"TIMEOUT after {elapsed:.1f}s")
except Exception as e:
    elapsed = time.time() - start
    print(f"ERROR after {elapsed:.1f}s: {type(e).__name__}: {e}")
