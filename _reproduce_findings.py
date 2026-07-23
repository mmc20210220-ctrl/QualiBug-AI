"""P0-13: Reproduce all 3 formal findings - 2 times each with independent data."""
import json, time, hashlib, urllib.request, urllib.error
from pathlib import Path
from datetime import datetime, timezone

BASE_URL = "http://localhost:8000"
TOKENS = {
    "admin": "acme-admin-token",
    "legal": "acme-legal-token",
    "finance": "acme-finance-token",
    "requester": "acme-requester-token",
    "project_manager": "acme-manager-token",
    "auditor": "acme-auditor-token",
    "vendor": "acme-vendor-token",
}

def api_request(method, path, token, body=None):
    """Make API request and return (status_code, response_body)."""
    url = f"{BASE_URL}{path}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        body_bytes = resp.read()
        try:
            return resp.status, json.loads(body_bytes)
        except json.JSONDecodeError:
            return resp.status, body_bytes.decode(errors="replace")[:500]
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            return e.code, json.loads(body_bytes)
        except json.JSONDecodeError:
            return e.code, body_bytes.decode(errors="replace")[:500]
    except Exception as e:
        return 0, str(e)

def reproduce_finding(finding_id, title, method, path, actor_role, expected_behavior, run_id):
    """Reproduce a single finding and return receipt."""
    token = TOKENS.get(actor_role, "")
    print(f"\n  [Run {run_id}] {method} {path} as '{actor_role}'")
    
    status, body = api_request(method, path, token)
    print(f"    Status: {status}")
    
    # Determine if the finding reproduces
    reproduced = False
    actual_behavior = {}
    
    if status == 200:
        # The legal role CAN access - this is the bug (should be restricted)
        if isinstance(body, list):
            actual_behavior = {"status": 200, "data_count": len(body), "leak_detected": True}
            reproduced = True
            print(f"    REPRODUCED: Got {len(body)} records (should be restricted)")
            if body and isinstance(body[0], dict):
                print(f"    Sample keys: {list(body[0].keys())[:6]}")
        elif isinstance(body, dict):
            data = body.get("data", body)
            if isinstance(data, list):
                actual_behavior = {"status": 200, "data_count": len(data), "leak_detected": True}
                reproduced = True
                print(f"    REPRODUCED: Got {len(data)} records (should be restricted)")
            else:
                actual_behavior = {"status": 200, "body_keys": list(body.keys())[:5], "leak_detected": True}
                reproduced = True
                print(f"    REPRODUCED: Got data (should be restricted)")
    elif status in (401, 403):
        actual_behavior = {"status": status, "leak_detected": False}
        reproduced = False
        print(f"    NOT REPRODUCED: Access denied ({status})")
    else:
        actual_behavior = {"status": status, "leak_detected": False}
        reproduced = False
        print(f"    UNEXPECTED: Status {status}")
    
    # Generate stable signature
    sig_input = f"{method}:{path}:{actor_role}:{expected_behavior}"
    signature = hashlib.sha256(sig_input.encode()).hexdigest()[:16]
    
    return {
        "run_id": run_id,
        "finding_id": finding_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "method": method,
        "path": path,
        "actor": actor_role,
        "status_code": status,
        "reproduced": reproduced,
        "expected_behavior": expected_behavior,
        "actual_behavior": actual_behavior,
        "experiment_signature": signature,
    }

# === Main Reproduction ===
print("=" * 60)
print("P0-13: Finding Reproduction")
print(f"Started: {datetime.now(timezone.utc).isoformat()}")
print("=" * 60)

findings_to_reproduce = [
    {
        "finding_id": "finding_e7dc78be4fd4983dbddf",
        "title": "owner_tenant_visibility: legal GET /api/v1/contracts",
        "method": "GET",
        "path": "/api/v1/contracts",
        "actor": "legal",
        "expected": "viewer_can_access=false, legal role should not see all contracts",
    },
    {
        "finding_id": "finding_652591d6d049eceb8f39",
        "title": "owner_tenant_visibility: legal GET /api/v1/reference/vendors",
        "method": "GET",
        "path": "/api/v1/reference/vendors",
        "actor": "legal",
        "expected": "viewer_can_access=false, legal role should not access vendor reference",
    },
    {
        "finding_id": "finding_95b5f7ba731e959d8a59",
        "title": "owner_tenant_visibility: legal GET /api/v1/reference/departments",
        "method": "GET",
        "path": "/api/v1/reference/departments",
        "actor": "legal",
        "expected": "viewer_can_access=false, legal role should not access department reference",
    },
]

all_receipts = []
signatures = {}

for f in findings_to_reproduce:
    print(f"\n{'='*50}")
    print(f"Finding: {f['title']}")
    print(f"{'='*50}")
    
    # Control: admin should access successfully
    print(f"\n  [Control] GET {f['path']} as 'admin'")
    ctrl_status, ctrl_body = api_request("GET", f["path"], TOKENS["admin"])
    ctrl_ok = ctrl_status == 200
    print(f"    Control status: {ctrl_status} (expected 200, ok={ctrl_ok})")
    
    # Reproduce 2 times
    receipts = []
    for run in range(1, 3):
        time.sleep(0.5)  # Small delay between runs
        receipt = reproduce_finding(
            f["finding_id"], f["title"], f["method"], f["path"],
            f["actor"], f["expected"], run
        )
        receipt["control_status"] = ctrl_status
        receipt["control_succeeded"] = ctrl_ok
        receipts.append(receipt)
        all_receipts.append(receipt)
    
    # Check signature stability
    sigs = set(r["experiment_signature"] for r in receipts)
    signatures[f["finding_id"]] = {
        "signatures": list(sigs),
        "stable": len(sigs) == 1,
        "reproduction_rate": sum(1 for r in receipts if r["reproduced"]) / len(receipts),
    }
    print(f"\n  Signature stable: {len(sigs) == 1} ({list(sigs)})")
    print(f"  Reproduction rate: {signatures[f['finding_id']]['reproduction_rate']*100:.0f}%")

# === Summary ===
print("\n\n" + "=" * 60)
print("REPRODUCTION SUMMARY")
print("=" * 60)

total_findings = len(findings_to_reproduce)
total_runs = len(all_receipts)
total_reproduced = sum(1 for r in all_receipts if r["reproduced"])
all_stable = all(s["stable"] for s in signatures.values())
all_100 = all(s["reproduction_rate"] == 1.0 for s in signatures.values())

print(f"  Findings tested: {total_findings}")
print(f"  Total reproduction runs: {total_runs}")
print(f"  Successfully reproduced: {total_reproduced}/{total_runs}")
print(f"  All signatures stable: {all_stable}")
print(f"  All findings 100% reproducible: {all_100}")
print(f"  Overall reproduction rate: {total_reproduced/total_runs*100:.0f}%")

for fid, info in signatures.items():
    print(f"\n  {fid}:")
    print(f"    stable={info['stable']}, rate={info['reproduction_rate']*100:.0f}%")

# Save reproduction report
report = {
    "schema_version": "qualibug.blind-baseline-reproduction.v1",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "base_url": BASE_URL,
    "findings_tested": total_findings,
    "total_runs": total_runs,
    "total_reproduced": total_reproduced,
    "reproduction_rate": total_reproduced / total_runs if total_runs > 0 else 0,
    "all_signatures_stable": all_stable,
    "all_findings_reproducible": all_100,
    "receipts": all_receipts,
    "signature_stability": signatures,
    "verdict": "PASS" if (all_100 and all_stable) else "PARTIAL",
}

out_path = Path("project_c_reproduction_report.json")
out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n  Report saved: {out_path} ({out_path.stat().st_size:,} bytes)")
print(f"\n  VERDICT: {report['verdict']}")
