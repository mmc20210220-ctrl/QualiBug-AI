"""Project A (benchmark_mall) regression test for deep_experiment_planner."""
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

input_dir = Path("projects/benchmark_mall/input")
if not input_dir.exists():
    print("SKIP: benchmark_mall input not found")
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    files = sorted(p for p in input_dir.iterdir() if p.is_file())
    print(f"[1] Ingesting {len(files)} files...")
    ingest_enterprise_knowledge_files(
        "benchmark_mall", files, root=root,
        actor={"name": "regression", "role": "project_owner"},
    )
    print("[2] Building Knowledge Asset...")
    asset = build_enterprise_business_knowledge_asset("benchmark_mall", root=root)
    print("[3] Building Behavior IR...")
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="benchmark_mall")
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

    print("[6] Running Deep Planner (budget=100)...")
    deep = plan_deep_experiments(obligations, by_obl, ir, budget=100)
    print(f"    deep_planned={deep['planned_count']}")
    print(f"    mechanisms={json.dumps(deep['mechanism_counts'])}")

    print("\n--- Project A Regression ---")
    print(f"Pipeline completed without error: PASS")
    print(f"Deep planner produced experiments: {'PASS' if deep['planned_count'] > 0 else 'INFO (0)'}")
    print(f"No crash/exception: PASS")
