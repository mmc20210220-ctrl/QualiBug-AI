"""Run a full scan and capture Oracle activation results."""
import hashlib, json, os, sys, time
from pathlib import Path

os.environ['QUALIBUG_JWT_SECRET'] = 'local-dev-only'
os.environ['QUALIBUG_PAGE_AGENT_BRIDGE_URL'] = 'http://127.0.0.1:8797/execute'
os.environ['QUALIBUG_PAGE_AGENT_BRIDGE_MODE'] = 'page_agent_browser_plan'
os.environ['QUALIBUG_PAGE_AGENT_BRIDGE_AUTO_START'] = 'true'
os.environ['ENABLE_V12_STATE_GRAPH_ENGINE'] = 'true'

root = Path("D:/QualiBug-AI/QualiBug-AI-main")
project = "benchmark_mall"

# Load input files
input_dir = root / "projects" / project / "input"
prd_text = (input_dir / "PRD.md").read_text(encoding="utf-8")
api_spec_text = (input_dir / "API_SPEC.md").read_text(encoding="utf-8")

# Compute source hash for provenance
source_hash = hashlib.sha256(api_spec_text.encode("utf-8")).hexdigest()

base_url = "http://127.0.0.1:8080"
campaign_context = {
    "target_id": "benchmark_mall_gateway",
    "scope_id": "benchmark_mall_gateway",
    "environment_id": "benchmark_mall_test",
    "environment_ref": "benchmark_mall_test",
    "environment_type": "test",
    "environment_kind": "test",
    "execution_mode": "approved_sandbox_write",
    "source_manifest": {
        "source_id": "benchmark_mall_api_spec_v1",
        "source_hash": source_hash,
        "source_version_id": f"v1_{source_hash[:24]}",
        "source_origin": "inline_test_harness",
    },
}

from ai_test_asset_center.__main__ import scan

print("=" * 60)
print(f"Starting scan: project={project}, base_url={base_url}")
print(f"Source hash: {source_hash[:16]}...")
print("=" * 60)

t0 = time.time()
result = scan(
    project=project,
    root=root,
    prd_text=prd_text,
    api_doc_text=api_spec_text,
    base_url=base_url,
    campaign_context=campaign_context,
)
elapsed = time.time() - t0

print(f"\nScan completed in {elapsed:.1f}s")

# Extract key metrics
campaign = result.get("campaign", {})
obligation_attempts = campaign.get("obligation_attempts", [])
total = len(obligation_attempts)

# Classify by terminal_status
blocked = [a for a in obligation_attempts if a.get("terminal_status") == "BLOCKED"]
hf = [a for a in obligation_attempts if a.get("terminal_status") == "HARNESS_FAILED"]
executed_list = [a for a in obligation_attempts if a.get("terminal_status") == "EXECUTED"]
deliverable = [a for a in obligation_attempts if a.get("customer_delivery_status") == "deliverable"]

print(f"\nAttempt summary: {total} total")
print(f"  BLOCKED:        {len(blocked)}")
print(f"  HARNESS_FAILED: {len(hf)}")
print(f"  EXECUTED:       {len(executed_list)}")
print(f"  DELIVERABLE:    {len(deliverable)}")

# Per-family breakdown
from collections import Counter
families = Counter(a.get("risk_family", "?") for a in obligation_attempts)
print(f"\nBy risk family:")
for fam, cnt in families.most_common():
    success = sum(1 for a in obligation_attempts if a.get("risk_family") == fam and a.get("customer_delivery_status") == "deliverable")
    print(f"  {fam}: {cnt} total, {success} deliverable ({100*success//cnt if cnt else 0}%)")

# Formal count projection
fcp = campaign.get("formal_count_projection", {})
print(f"\nFormal deliverables: {fcp.get('formal_customer_deliverable_count', 0)}")
print(f"Canonical defects:   {fcp.get('canonical_defect_count', 0)}")

# Oracle activation detail
oracle_activations = campaign.get("oracle_activation_receipts", [])
if oracle_activations:
    act_statuses = Counter(a.get("status", "?") for a in oracle_activations)
    print(f"\nOracle activation statuses: {dict(act_statuses)}")

# Save results
out_file = root / "platform_outputs" / "_last_scan_test.json"
with open(out_file, 'w') as f:
    json.dump(result, f, indent=2, default=str)
print(f"\nFull result saved to: {out_file}")
