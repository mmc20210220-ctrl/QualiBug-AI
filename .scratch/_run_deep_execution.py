"""Deep Experiment Execution Bridge - Small Scale Run (≤30).

Runs adapted deep experiments through the existing execute_selected_experiments
chain against the ContractFlow mock server (localhost:8000).
"""
import json, sys, time, tempfile, hashlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_test_asset_center.enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_files,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.experiment_compiler import compile_experiments
from ai_test_asset_center.deep_experiment_planner import plan_deep_experiments
from ai_test_asset_center.deep_experiment_protocol_adapter import adapt_deep_experiments_for_execution
from ai_test_asset_center.experiment_batch_executor import execute_selected_experiments
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract

BASE_URL = "http://localhost:8000"
PROJECT = "contractflow_project_c"
CAMPAIGN_ID = f"deep_exec_small_{int(time.time())}"

def _stable_id(*parts):
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]

print("=" * 60)
print("DEEP EXPERIMENT EXECUTION BRIDGE - SMALL SCALE")
print("=" * 60)

# ── Phase 1: Build IR and plan ──
print("\n[1] Building Behavior IR...")
input_dir = Path("projects/contractflow_c/input")
files = sorted(p for p in input_dir.iterdir() if p.is_file())
with tempfile.TemporaryDirectory() as tmp:
    root_tmp = Path(tmp)
    ingest_enterprise_knowledge_files("contractflow_c", files, root=root_tmp, actor={"name": "diag", "role": "project_owner"})
    asset = build_enterprise_business_knowledge_asset("contractflow_c", root=root_tmp)
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="contractflow_c")

print(f"    actors={len(ir.get('actors',[]))}, ops={len(ir.get('operations',[]))}")

print("\n[2] Compiling obligations...")
obls = compile_obligations_from_behavior_ir(ir)
obligations = obls["obligations"]
print(f"    obligations={len(obligations)}")

print("\n[3] Compiling standard experiments...")
exps = compile_experiments(obligations, behavior_ir=ir)
by_obligation = exps.get("by_obligation") or exps.get("experiments_by_obligation") or {}
print(f"    standard compiled={len(by_obligation)}")

print("\n[4] Running Deep Planner (budget=100)...")
deep = plan_deep_experiments(obligations, by_obligation, ir, budget=100)
raw_experiments = deep.get("deep_experiments", [])
print(f"    deep_planned={len(raw_experiments)}")
print(f"    mechanisms={json.dumps(deep.get('mechanism_counts', {}))}")

print("\n[5] Adapting for execution...")
adaptation = adapt_deep_experiments_for_execution(raw_experiments, ir)
adapted = adaptation["adapted"]
adapted_by_obl = adaptation["by_obligation"]
print(f"    adapted={len(adapted)}, blocked={adaptation['blocked_count']}")

# Merge adapted deep experiments into by_obligation for the executor
for oid, exp in adapted_by_obl.items():
    by_obligation[oid] = exp

# ── Phase 2: Prepare execution parameters ──
print("\n[6] Preparing execution context...")
run_id = _stable_id("run", CAMPAIGN_ID)
mainline_run = dict(build_mainline_run_contract(
    mainline_authority="experiment_candidate",
    run_id=run_id,
    campaign_id=CAMPAIGN_ID,
    target_id="contractflow_c",
    environment_id="contractflow_c_test",
    policy_version="v1",
    evaluation_mode="operational",
))
runtime_contract = {
    "schema_version": "qualibug.runtime-contract.v1",
    "environment_type": "test",
    "environment_ref": "contractflow_c_test_env",
    "validation_phase": "small_scale",
    "write_allowed": True,
    "read_only": False,
    "status": "approved",
    "approved_base_url": BASE_URL,
    "requested_base_url": BASE_URL,
    "execution_mode": "approved_sandbox_write",
}

# Build selected list from adapted experiments (formal run up to 100)
selected = [
    {"obligation_id": oid, "experiment_id": exp.get("experiment_id", ""), "candidate_id": _stable_id("cand", PROJECT, oid)}
    for oid, exp in list(adapted_by_obl.items())[:100]
]
print(f"    selected={len(selected)}")
print(f"    campaign_id={CAMPAIGN_ID}")
print(f"    base_url={BASE_URL}")

# ── Phase 3: Execute ──
print("\n[7] Executing via execute_selected_experiments...")
print("    (this makes real HTTP calls to the mock server)")
start_time = time.time()

try:
    result = execute_selected_experiments(
        selected,
        experiments_by_obligation=by_obligation,
        behavior_ir=ir,
        root=Path("."),
        project=PROJECT,
        base_url=BASE_URL,
        runtime_contract=runtime_contract,
        mainline_run=mainline_run,
        campaign_id=CAMPAIGN_ID,
        experiment_budget=100,
        validation_phase="formal",
    )
except Exception as exc:
    print(f"\n    EXECUTION ERROR: {type(exc).__name__}: {exc}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

elapsed = time.time() - start_time

# ── Phase 4: Report ──
print(f"\n[8] Results (elapsed={elapsed:.1f}s):")
print(f"    total_results={len(result.get('results', []))}")
print(f"    executed={result.get('executed', 0)}")
print(f"    blocked={result.get('blocked', 0)}")
print(f"    harness_errors={result.get('harness_errors', 0)}")
print(f"    findings={len(result.get('findings', []))}")
print(f"    cleanup_failures={result.get('cleanup_failures', 0)}")

# Detailed status breakdown
statuses = {}
reason_codes = {}
for r in result.get("results", []):
    s = r.get("status", "UNKNOWN")
    statuses[s] = statuses.get(s, 0) + 1
    rc = r.get("reason_code", "")
    if rc:
        reason_codes[rc] = reason_codes.get(rc, 0) + 1

print(f"\n    status_breakdown={json.dumps(statuses)}")
if reason_codes:
    print(f"    reason_codes={json.dumps(reason_codes)}")

# Show findings
findings = result.get("findings", [])
if findings:
    print(f"\n[9] FINDINGS ({len(findings)}):")
    for f in findings[:10]:
        print(f"    - {f.get('finding_id','')[:20]} | {f.get('title','')[:60]} | severity={f.get('severity','')} | confidence={f.get('confidence','')}")
else:
    print("\n[9] No findings generated.")

# Show sample executed results
executed_results = [r for r in result.get("results", []) if r.get("status") == "EXECUTED"]
if executed_results:
    print(f"\n[10] Sample EXECUTED results ({len(executed_results)} total):")
    for r in executed_results[:5]:
        print(f"    - obl={r.get('obligation_id','')[:20]} | exp={r.get('experiment_id','')[:20]} | oracle={r.get('oracle_verdict','')} | finding={bool(r.get('finding'))}")

# Save full results
output_file = Path("deep_experiment_execution_results.json")
# Redact large fields for storage
save_result = {
    "schema_version": "qualibug.deep-experiment-execution-bridge.v1",
    "campaign_id": CAMPAIGN_ID,
    "base_url": BASE_URL,
    "project": PROJECT,
    "elapsed_seconds": round(elapsed, 1),
    "summary": {
        "total_selected": len(selected),
        "executed": result.get("executed", 0),
        "blocked": result.get("blocked", 0),
        "harness_errors": result.get("harness_errors", 0),
        "findings_count": len(findings),
        "status_breakdown": statuses,
        "reason_codes": reason_codes,
    },
    "findings": findings[:20],
    "results_sample": result.get("results", [])[:10],
}
json.dump(save_result, open(output_file, "w", encoding="utf-8"), indent=2, ensure_ascii=False, default=str)
print(f"\n[11] Results saved to {output_file}")
print("\n" + "=" * 60)
print("DONE")
