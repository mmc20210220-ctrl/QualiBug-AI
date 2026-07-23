"""Project C Blind Baseline Scan - ContractFlow."""
import os, sys, json, time
from pathlib import Path

# Disable LLM (frozen engine, no external calls)
os.environ["LLM_BASE_URL"] = ""
os.environ["LLM_API_KEY"] = ""
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
    "source_id": f"contractflow_c_blind_{int(time.time())}",
    "source_hash": source_hash,
    "source_origin": "blind_test_package",
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

print(f"=== Project C Blind Baseline Scan ===")
print(f"Project: {project}")
print(f"Base URL: {base_url}")
print(f"API spec: {len(api_doc_text):,} chars")
print(f"PRD: {len(prd_text):,} chars")
print(f"Source hash: {source_hash[:16]}...")
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
print(f"Grade: {result.get('grade')}")
print(f"Total findings: {result.get('total_findings')}")
print(f"Total candidates: {result.get('total_candidates')}")

# Execution status
layers = result.get("layers", {})
src_layer = layers.get("source_grounded_discovery", {})
print(f"Execution status: {src_layer.get('execution_status')}")

# Funnel
funnel = result.get("discovery_funnel", {})
print(f"\n--- Discovery Funnel ---")
for key in ("rules_generated", "rules_grounded", "rules_executable",
            "obligations_generated", "obligations_planned", "fixtures_ready",
            "executed", "before_observed", "after_observed",
            "oracle_evaluated", "candidate_findings", "formal_findings"):
    val = funnel.get(key)
    if val is not None:
        print(f"  {key}: {val}")

# Behavior IR
v12 = result.get("v12_result", {})
bir = v12.get("behavior_ir", {})
print(f"\n--- Behavior IR ---")
print(f"  Operations: {len(bir.get('operations', []))}")
print(f"  Entities: {len(bir.get('entities', []))}")
print(f"  States: {len(bir.get('states', []))}")
print(f"  Invariants: {len(bir.get('invariants', []))}")
print(f"  Actors: {len(bir.get('actors', []))}")
print(f"  Relations: {len(bir.get('relations', []))}")

# Obligation attempts
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"\n--- Obligation Attempts ---")
print(f"  Total: {len(attempts)}")
reasons = {}
for a in attempts:
    r = a.get("terminal_reason", "unknown")
    reasons[r] = reasons.get(r, 0) + 1
for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
    print(f"  {r}: {c}")

# Candidates
candidates = result.get("candidates", [])
if candidates:
    print(f"\n--- Candidates ({len(candidates)}) ---")
    for i, c in enumerate(candidates[:10]):
        print(f"  [{i+1}] {c.get('title', 'N/A')[:80]}")
        print(f"      rule_type={c.get('rule_type')}, confidence={c.get('confidence')}")

# Findings
findings = result.get("findings", [])
if findings:
    print(f"\n--- Findings ({len(findings)}) ---")
    for i, f in enumerate(findings[:10]):
        print(f"  [{i+1}] {f.get('title', 'N/A')[:80]}")

# Save result
out_path = root / "_scan_project_c_result.json"
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nResult saved: {out_path} ({out_path.stat().st_size:,} bytes)")
