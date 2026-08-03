"""V1.5.1 Phase 4: Trigger formal product entry scan."""
import json
import urllib.request
import datetime
import sys

now = datetime.datetime.now().isoformat()
out_dir = "artifacts/spec_v1_5_1"

# Trigger scan through formal product entry
scan_body = json.dumps({
    "project_id": "benchmark_mall_131",
    "base_url": "http://localhost:8080",
    "approved_base_url": "http://localhost:8080",
    "environment_type": "test",
    "environment_ref": "sandbox",
    "target_id": "benchmark_mall_131_sandbox",
}).encode("utf-8")

req = urllib.request.Request(
    "http://localhost:8088/api/v1/scan",
    data=scan_body,
    headers={"Content-Type": "application/json"},
    method="POST",
)

print(f"[{now}] Triggering formal scan via POST /api/v1/scan ...")
try:
    resp = urllib.request.urlopen(req, timeout=600)
    status = resp.status
    data = json.loads(resp.read().decode("utf-8"))
    print(f"Response status: {status}")
    print(f"Response ok: {data.get('ok')}")
    
    # Save entry receipt
    entry_receipt = {
        "spec_version": "V1.5.1",
        "triggered_at": now,
        "completed_at": datetime.datetime.now().isoformat(),
        "entry_request": {
            "method": "POST",
            "url": "http://localhost:8088/api/v1/scan",
            "body": {"project_id": "benchmark_mall_131"},
        },
        "entry_response_status": status,
        "entry_response_ok": data.get("ok"),
        "campaign_id": data.get("campaign_id") or data.get("data", {}).get("campaign_id"),
        "run_id": data.get("run_id") or data.get("data", {}).get("run_id"),
        "mainline_authority_id": data.get("mainline_authority_id") or data.get("data", {}).get("mainline_authority_id"),
        "scan_result_keys": sorted(data.keys()) if isinstance(data, dict) else [],
    }
    
    with open(f"{out_dir}/v151_start_manifest.json", "w", encoding="utf-8") as f:
        json.dump(entry_receipt, f, indent=2, ensure_ascii=False)
    
    # Save full response for analysis
    with open(f"{out_dir}/v151_scan_response.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"Campaign ID: {entry_receipt['campaign_id']}")
    print(f"Run ID: {entry_receipt['run_id']}")
    print("Entry receipt saved")
    
except Exception as e:
    print(f"ERROR: {e}")
    error_receipt = {
        "spec_version": "V1.5.1",
        "triggered_at": now,
        "error": str(e),
        "BLOCK_REASON": "SCAN_ENTRY_FAILED",
    }
    with open(f"{out_dir}/v151_start_manifest.json", "w", encoding="utf-8") as f:
        json.dump(error_receipt, f, indent=2, ensure_ascii=False)
    sys.exit(1)
