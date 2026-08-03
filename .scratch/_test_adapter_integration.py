"""Test: Deep Experiment Protocol Adapter integration with Project C."""
import json, sys, tempfile
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

input_dir = Path("projects/contractflow_c/input")
if not input_dir.exists():
    print("SKIP: contractflow_c input not found")
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    files = sorted(p for p in input_dir.iterdir() if p.is_file())
    print(f"[1] Ingesting {len(files)} files...")
    ingest_enterprise_knowledge_files(
        "contractflow_c", files, root=root,
        actor={"name": "diag", "role": "project_owner"},
    )
    print("[2] Building Knowledge Asset...")
    asset = build_enterprise_business_knowledge_asset("contractflow_c", root=root)
    print("[3] Building Behavior IR...")
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="contractflow_c")
    print(f"    actors={len(ir.get('actors',[]))}, ops={len(ir.get('operations',[]))}")
    print("[4] Compiling Obligations...")
    obl_result = compile_obligations_from_behavior_ir(ir)
    obligations = obl_result.get("obligations", [])
    print(f"    obligations={len(obligations)}")
    print("[5] Compiling Experiments...")
    exp_pack = compile_experiments(obligations, behavior_ir=ir, environment_type="staging")
    compiled = exp_pack.get("compiled_count", 0)
    blocked = exp_pack.get("blocked_count", 0)
    print(f"    compiled={compiled}, blocked={blocked}")

    by_obl = {}
    for row in (exp_pack.get("experiments", []) + exp_pack.get("blocked_experiments", [])):
        oid = str(row.get("obligation_id") or "")
        if oid:
            by_obl[oid] = row

    print("[6] Running Deep Planner (budget=30)...")
    deep = plan_deep_experiments(obligations, by_obl, ir, budget=30)
    print(f"    deep_planned={deep['planned_count']}")
    print(f"    mechanisms={json.dumps(deep['mechanism_counts'])}")

    # Check actor selection
    sample = deep["deep_experiments"][0] if deep["deep_experiments"] else {}
    ctrl = (sample.get("control_plan") or [{}])[0]
    print(f"    sample actor_ref={ctrl.get('actor_ref')}")

    print("[7] Running Protocol Adapter...")
    adapt = adapt_deep_experiments_for_execution(deep["deep_experiments"], ir)
    print(f"    adapted={adapt['adapted_count']}")
    print(f"    blocked={adapt['blocked_count']}")
    print(f"    block_reasons={json.dumps(adapt['blocked_reasons'])}")
    print(f"    actor_resolution={json.dumps(adapt['actor_resolution'])}")

    adapted = adapt["adapted"]
    has_bp = sum(1 for e in adapted if e.get("binding_plan"))
    has_sc = sum(1 for e in adapted if e.get("safety_contract"))
    has_cl = sum(1 for e in adapted if e.get("cleanup_plan"))
    has_as = sum(1 for e in adapted if e.get("assertions"))
    has_fd = sum(1 for e in adapted if e.get("fixture_dag"))
    print(f"    with binding_plan={has_bp}")
    print(f"    with safety_contract={has_sc}")
    print(f"    with cleanup_plan={has_cl}")
    print(f"    with assertions={has_as}")
    print(f"    with fixture_dag={has_fd}")

    # Verify preflight would pass
    print("[8] Simulating preflight checks...")
    from ai_test_asset_center.experiment_runtime_support import preflight_experiment_executable, load_actor_tokens
    # Load real tokens from platform_inputs
    real_root = Path(".")
    tokens = load_actor_tokens(real_root, "contractflow_project_c")
    print(f"    loaded tokens: {len(tokens)} keys")
    if tokens:
        print(f"    sample keys: {list(tokens.keys())[:6]}")
    pass_count = 0
    fail_reasons = {}
    for exp in adapted[:30]:
        ok, reason, detail = preflight_experiment_executable(
            exp, behavior_ir=ir, actor_tokens=tokens, best_effort=True
        )
        if ok:
            pass_count += 1
        else:
            fail_reasons[reason] = fail_reasons.get(reason, 0) + 1

    print(f"    preflight PASS (best_effort): {pass_count}/{len(adapted[:30])}")
    print(f"    preflight FAIL reasons: {json.dumps(fail_reasons)}")

    print("\n--- Adapter Integration Test ---")
    print(f"Deep planner produces plans: {'PASS' if deep['planned_count'] > 0 else 'FAIL'}")
    print(f"Adapter enriches plans: {'PASS' if adapt['adapted_count'] > 0 else 'FAIL'}")
    print(f"Actor resolved (non-template): {'PASS' if adapt['actor_resolution']['resolved'] > 0 else 'FAIL'}")
    print(f"Binding plans added: {'PASS' if has_bp > 0 else 'INFO (0)'}")
    print(f"Preflight pass rate: {pass_count}/{len(adapted[:30])}")
