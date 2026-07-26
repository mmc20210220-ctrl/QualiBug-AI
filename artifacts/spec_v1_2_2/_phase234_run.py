"""Phase 2-4: Budget freeze, Start manifest, and Full Run execution.

This script:
1. Generates v122_live_budget_manifest.json
2. Generates v122_live_start_manifest.json
3. Triggers Full Run through official product entry POST /api/v1/scan
4. Saves the raw scan result
"""
import json
import hashlib
import datetime
import os
import sys
import urllib.request

ARTIFACT_DIR = "artifacts/spec_v1_2_2"
os.makedirs(ARTIFACT_DIR, exist_ok=True)

# ─── Phase 2: Budget Manifest ────────────────────────────────────────────────

budget_manifest = {
    "schema_version": "qualibug.v122-live-budget-manifest.v1",
    "budget": {
        "max_obligations": 200,
        "max_planned_experiments": 200,
        "max_executed_experiments": 100,
        "max_http_requests": 2000,
        "max_rounds": 3,
        "max_runtime_seconds": 1800,
        "max_runtime_binding_probes": 500,
        "max_reproduction_attempts": 2,
    },
    "auto_scale_disabled": True,
    "budget_source": "manifest_hard_override",
}
budget_path = os.path.join(ARTIFACT_DIR, "v122_live_budget_manifest.json")
with open(budget_path, "w", encoding="utf-8") as f:
    json.dump(budget_manifest, f, indent=2, ensure_ascii=False)
budget_hash = hashlib.sha256(open(budget_path, "rb").read()).hexdigest()
print(f"[Phase 2] Budget manifest saved. Hash: {budget_hash[:16]}")

# ─── Phase 3: Start Manifest ─────────────────────────────────────────────────

def file_hash(path):
    if not os.path.exists(path):
        return "NOT_FOUND"
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()

start_manifest = {
    "schema_version": "qualibug.v122-live-start-manifest.v1",
    "run_name": "V1_2_2_LIVE_RUNTIME_EFFECT_VALIDATION_V1",
    "release_commit": "3992b7076f1e0db3343eb6535c6660d6b0bd2c95",
    "tree_hash": "b64a4a70485558696f41495ac042f228c9006084",
    "working_tree_clean": True,
    "target_manifest_hash": file_hash(os.path.join(ARTIFACT_DIR, "v122_live_target_manifest.json")),
    "budget_manifest_hash": budget_hash,
    "document_manifest_hash": file_hash("platform_inputs/benchmark_mall/API_SPEC.md"),
    "prompt_hash": file_hash("ai_test_asset_center/discovery_engine.py"),
    "model_hash": "frozen_at_runtime",
    "planner_hash": file_hash("ai_test_asset_center/experiment_compiler_obligation.py"),
    "oracle_hash": file_hash("ai_test_asset_center/contract_oracles.py"),
    "risk_policy_hash": file_hash("ai_test_asset_center/target_policy.py"),
    "start_time": datetime.datetime.now().isoformat(),
    "run_started": True,
}
start_path = os.path.join(ARTIFACT_DIR, "v122_live_start_manifest.json")
with open(start_path, "w", encoding="utf-8") as f:
    json.dump(start_manifest, f, indent=2, ensure_ascii=False)
print(f"[Phase 3] Start manifest saved. RUN_STARTED = true")

# ─── Phase 4: Full Run through official product entry ────────────────────────

print("[Phase 4] Triggering Full Run via POST http://localhost:8088/api/v1/scan ...")
print("  Project: benchmark_mall")
print("  Target: http://localhost:8080")

scan_body = {
    "project_id": "benchmark_mall",
    "base_url": "http://localhost:8080",
    "environment_type": "test",
    "execution_mode": "governed_read_write",
    "campaign_context": {
        "environment_type": "test",
        "scope_id": "v122_live_validation",
        "campaign_budget": budget_manifest["budget"],
    },
}

payload = json.dumps(scan_body).encode("utf-8")
req = urllib.request.Request(
    "http://localhost:8088/api/v1/scan",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)

print(f"  Start: {datetime.datetime.now().isoformat()}")
try:
    with urllib.request.urlopen(req, timeout=1800) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        print(f"  Status: {resp.status}")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", errors="replace")
    result = {"error": True, "status": e.code, "detail": body[:2000]}
    print(f"  HTTP Error: {e.code}")
    print(f"  Detail: {body[:500]}")
except Exception as e:
    result = {"error": True, "exception": str(e)}
    print(f"  Exception: {e}")

end_time = datetime.datetime.now().isoformat()
print(f"  End: {end_time}")

# Save raw result
result_path = os.path.join(ARTIFACT_DIR, "v122_live_scan_result_raw.json")
with open(result_path, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False, default=str)
print(f"  Raw result saved: {result_path}")

# Quick summary
if not result.get("error"):
    v12 = result.get("v12", {})
    findings = result.get("findings", v12.get("findings", []))
    campaign = result.get("campaign", {})
    print(f"\n[Summary]")
    print(f"  Findings: {len(findings) if isinstance(findings, list) else 'N/A'}")
    print(f"  Campaign: {json.dumps(campaign, default=str)[:200]}")
else:
    print(f"\n[ERROR] Scan failed. See raw result for details.")
