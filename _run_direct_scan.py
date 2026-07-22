"""Run scan directly through the pipeline (bypasses HTTP server to use fresh code)."""
import json, sys, time
sys.path.insert(0, ".")

from pathlib import Path

root = Path(".")
project = "benchmark_mall_131"

# Load knowledge asset
asset_path = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
asset = json.loads(asset_path.read_text(encoding="utf-8"))

# Load test accounts
accounts_path = root / "platform_inputs" / project / "test_accounts.json"
accounts_data = json.loads(accounts_path.read_text(encoding="utf-8"))
rows = accounts_data.get("accounts", [])
runtime_actors = []
for row in rows:
    role = row.get("authenticated_role") or row.get("role") or row.get("name") or row.get("id")
    account_ref = row.get("account_ref") or row.get("email") or row.get("username") or row.get("id") or role
    runtime_actors.append({
        "role": role,
        "account_ref": account_ref,
        "secret_ref": f"secret_ref:test_accounts:{account_ref}",
        "status": row.get("status") or "active",
    })

print(f"Building Behavior IR...")
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
bir = build_behavior_ir_from_knowledge_asset(
    asset, project_id=project,
    api_operations=asset.get("interfaces", []),
    runtime_actors=runtime_actors,
)
ops = bir.get("operations", [])
print(f"  IR operations: {len(ops)}")

print(f"\nGenerating obligations...")
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
obl_result = compile_obligations_from_behavior_ir(bir)
obligations = obl_result.get("obligations", []) if isinstance(obl_result, dict) else obl_result
print(f"  Obligations generated: {len(obligations)}")

print(f"\nCompiling experiments...")
from ai_test_asset_center.experiment_compiler_base import compile_experiments
compile_result = compile_experiments(obligations, behavior_ir=bir, environment_type="test")
compiled_count = compile_result.get("compiled_count", 0)
blocked_count = compile_result.get("blocked_count", 0)
block_reasons = compile_result.get("block_reason_counts", {})
print(f"  Compiled: {compiled_count}")
print(f"  Blocked: {blocked_count}")
print(f"  Block reasons: {json.dumps(block_reasons, indent=4)}")

# Build experiments_by_obligation
experiments_by_obligation = {}
for exp in compile_result.get("experiments", []):
    oid = exp.get("obligation_id", "")
    if oid:
        experiments_by_obligation[oid] = exp

print(f"\nPlanning execution (budget auto-scale)...")
from ai_test_asset_center.adaptive_discovery_planner import plan_obligation_round
from ai_test_asset_center.pipeline_slices import _auto_scale_slice_budget
budget = _auto_scale_slice_budget(compiled_count)
print(f"  Auto-scaled budget: {budget}")

plan = plan_obligation_round(
    obligations,
    experiments_by_obligation=experiments_by_obligation,
    behavior_ir=bir,
    budget=budget,
)
selected = plan.get("selected", [])
pending = plan.get("pending_next_round", [])
print(f"  Selected: {len(selected)}")
print(f"  Pending next round: {len(pending)}")

# Family distribution of selected
families = {}
for item in selected:
    fam = item.get("risk_family", "?")
    families[fam] = families.get(fam, 0) + 1
print(f"  Selected by family: {json.dumps(families, indent=4)}")

print(f"\n=== COMPILE IMPROVEMENT SUMMARY ===")
print(f"  Previous: 234 compiled, 456 blocked")
print(f"  Current:  {compiled_count} compiled, {blocked_count} blocked")
print(f"  Improvement: +{compiled_count - 234} more experiments compilable")
