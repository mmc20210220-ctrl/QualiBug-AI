"""Phase 4: Execute Full Run through the official scan() product entry.

Uses ai_test_asset_center.__main__.scan — the canonical product entrypoint.
Passes enterprise materials from platform_inputs/benchmark_mall/.

Key fix: provide source_manifest (source_id + source_hash = SHA256 of api_doc_text)
and environment_ref in campaign_context to satisfy source provenance contract.
"""
import json
import hashlib
import datetime
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path for benchmark_evaluator imports
_PROJECT_ROOT = str(Path(__file__).resolve().parents[2])
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

ARTIFACT_DIR = Path("artifacts/spec_v1_2_2")
INPUT_DIR = Path("platform_inputs/benchmark_mall")

# Load enterprise materials — use ONLY the registered API_SPEC.md as api_doc_text
# The source_hash must equal SHA256 of the exact api_doc_text passed to scan().
api_doc_text = (INPUT_DIR / "API_SPEC.md").read_text(encoding="utf-8")
prd_text = (INPUT_DIR / "PRD.md").read_text(encoding="utf-8")

# Compute immutable source hash
source_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()
source_id = "src_fe4c9062370d3c58"  # Active markdown_api source from knowledge center

print(f"[Phase 4] Full Run via ai_test_asset_center.__main__.scan()")
print(f"  Project: benchmark_mall")
print(f"  Target: http://localhost:8080")
print(f"  API doc length: {len(api_doc_text)} chars")
print(f"  Source hash: {source_hash}")
print(f"  Source ID: {source_id}")
print(f"  PRD length: {len(prd_text)} chars")
print(f"  Start: {datetime.datetime.now().isoformat()}")

# Import and call the official scan entry
from ai_test_asset_center.__main__ import scan

campaign_context = {
    "environment_type": "test",
    "environment_ref": "benchmark_mall_local_test",
    "scope_id": "v122_live_validation",
    "execution_mode": "governed_read_write",
    "source_manifest": {
        "source_id": source_id,
        "source_hash": source_hash,
        "source_origin": "registered_source_registry",
    },
    "campaign_budget": {
        "max_obligations": 200,
        "max_planned_experiments": 200,
        "max_executed_experiments": 100,
        "max_http_requests": 2000,
        "max_rounds": 3,
        "max_runtime_seconds": 1800,
    },
}

result = scan(
    project="benchmark_mall",
    root=Path.cwd(),
    prd_text=prd_text,
    api_doc_text=api_doc_text,
    base_url="http://localhost:8080",
    multi_layer=True,
    save_report=True,
    campaign_context=campaign_context,
)

end_time = datetime.datetime.now().isoformat()
print(f"  End: {end_time}")

# Save raw result
result_path = ARTIFACT_DIR / "v122_live_scan_result_raw.json"
with open(result_path, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=2, ensure_ascii=False, default=str)
print(f"  Raw result saved: {result_path}")

# Summary
print(f"\n{'='*60}")
print(f"[RESULT SUMMARY]")
print(f"{'='*60}")
print(f"  ok: {result.get('ok')}")
print(f"  total_findings: {result.get('total_findings')}")
print(f"  total_ms: {result.get('total_ms')}")
print(f"  score: {result.get('score')}")
print(f"  coverage: {result.get('coverage')}")

v12 = result.get("v12", {})
if v12:
    print(f"\n  [V12 Pipeline]")
    print(f"  v12 keys: {sorted(v12.keys())[:15]}")
    findings = v12.get("findings", [])
    print(f"  v12 findings: {len(findings)}")
    mainline = v12.get("mainline_run", {})
    if mainline:
        print(f"  mainline_run: {json.dumps(mainline, default=str)[:200]}")

campaign = result.get("campaign", {})
if campaign:
    print(f"\n  [Campaign]")
    print(f"  campaign: {json.dumps(campaign, default=str)[:300]}")

# Check for experiments
experiments = result.get("experiments", v12.get("experiments", []))
if experiments:
    print(f"\n  [Experiments]: {len(experiments)}")

# Coverage funnel
funnel = result.get("execution_coverage_funnel", v12.get("execution_coverage_funnel", {}))
if funnel:
    print(f"\n  [Funnel]: {json.dumps(funnel, default=str)[:300]}")
