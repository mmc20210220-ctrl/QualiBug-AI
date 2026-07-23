"""Project C Post-Tuning Scan - after Oracle compilation fix (LLM ENABLED)."""
import os, sys, json, time
from pathlib import Path

# LLM ENABLED - do not disable
os.environ["QUALIBUG_INCREMENTAL_DISCOVERY_ROUND_LIMIT"] = "3"
os.environ["QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND"] = "8"
os.environ["QUALIBUG_MAX_CONCURRENCY"] = "8"
os.environ["PROBE_TIMEOUT_MS"] = "10000"

sys.path.insert(0, str(Path(__file__).parent))

from ai_test_asset_center.__main__ import scan

project = "contractflow_project_c"
root = Path(".")
base_url = "http://localhost:8000"
input_dir = root / "platform_inputs" / project

# Read API spec
api_spec_path = input_dir / "API_SPEC.md"
api_doc_text = api_spec_path.read_text(encoding="utf-8")

# Read PRD
prd_path = input_dir / "PRD.md"
prd_text = prd_path.read_text(encoding="utf-8") if prd_path.exists() else ""

# Source manifest
source_hash = __import__("hashlib").sha256(api_doc_text.encode("utf-8")).hexdigest()
source_manifest = {
    "source_id": f"contractflow_c_posttuning_{int(time.time())}",
    "source_hash": source_hash,
    "source_origin": "post_tuning_test",
}

# Campaign context
campaign_context = {
    "source_manifest": source_manifest,
    "scope_id": "contractflow_project_c",
    "environment_type": "test",
    "environment_ref": "contractflow-local",
    "execution_mode": "approved_sandbox_write",
    "blind_test_mode": True,
}

print(f"=== Project C Post-Tuning Scan ===")
print(f"Project: {project}")
print(f"Base URL: {base_url}")
print(f"Start: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print()

started = time.time()
result = scan(
    project=project,
    root=root,
    prd_text=prd_text,
    api_doc_text=api_doc_text,
    base_url=base_url,
    save_report=False,
    campaign_context=campaign_context,
)
elapsed = time.time() - started

print(f"\n=== Scan Complete ({elapsed:.1f}s) ===")
print(f"Success: {result.get('success')}")
print(f"Total findings: {result.get('total_findings')}")
print(f"Total candidates: {result.get('total_candidates')}")

# Save result (separate from blind baseline)
out_path = root / "project_c_post_tuning_result.json"
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nResult saved: {out_path} ({out_path.stat().st_size:,} bytes)")

# Quick summary of assertion statuses
v12 = result.get("v12_result", {})
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"\n--- Obligation Attempts: {len(attempts)} ---")
from collections import Counter
reasons = Counter(a.get("reason_code", "?") for a in attempts)
for r, c in reasons.most_common(15):
    print(f"  {r}: {c}")

# Check for new findings
findings = result.get("findings", [])
candidates = result.get("candidates", [])
print(f"\n--- Findings: {len(findings)}, Candidates: {len(candidates)} ---")
for f in findings[:10]:
    print(f"  [F] {f.get('title', 'N/A')[:80]}")
for c in candidates[:10]:
    print(f"  [C] {c.get('title', 'N/A')[:80]}")
