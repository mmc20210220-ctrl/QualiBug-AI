"""Quick integration test for deep_experiment_planner."""
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

input_dir = Path("projects/contractflow_c/input")
with tempfile.TemporaryDirectory() as tmp:
    root = Path(tmp)
    files = sorted(p for p in input_dir.iterdir() if p.is_file())
    ingest_enterprise_knowledge_files(
        "contractflow_c", files, root=root,
        actor={"name": "t", "role": "project_owner"},
    )
    asset = build_enterprise_business_knowledge_asset("contractflow_c", root=root)
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="contractflow_c")
    obl_result = compile_obligations_from_behavior_ir(ir)
    obligations = obl_result.get("obligations", [])
    exp_pack = compile_experiments(obligations, behavior_ir=ir, environment_type="staging")

    # Build by_obligation
    by_obl = {}
    for row in (exp_pack.get("experiments", []) + exp_pack.get("blocked_experiments", [])):
        oid = str(row.get("obligation_id") or "")
        if oid:
            by_obl[oid] = row

    # Run deep planner - Formal Post-Tuning (budget=100)
    deep = plan_deep_experiments(obligations, by_obl, ir, budget=100)
    print(f"Deep planned: {deep['planned_count']}")
    print(f"Mechanisms: {json.dumps(deep['mechanism_counts'], indent=2)}")
    print(f"Skipped: {deep['skipped_count']}")

    # Show first 10 experiments
    for exp in deep["deep_experiments"][:10]:
        print(f"  {exp['experiment_id']} [{exp['mechanism']}] obl={exp['obligation_id'][:24]}...")

    # Verify success criteria
    planned = deep["planned_count"]
    mechanisms_used = len(deep["mechanism_counts"])
    print(f"\n--- Success Criteria (Formal Post-Tuning) ---")
    print(f"Planned >= 7 targets: {'PASS' if planned >= 7 else 'FAIL'} ({planned})")
    print(f"Mechanisms >= 2: {'PASS' if mechanisms_used >= 2 else 'FAIL'} ({mechanisms_used})")
    print(f"Budget <= 100: {'PASS' if planned <= 100 else 'FAIL'} ({planned})")

    # Check for placeholder requests (should be 0)
    placeholder_count = 0
    for exp in deep["deep_experiments"]:
        for step in exp.get("treatment_plan", []):
            if isinstance(step, dict) and not step.get("operation_ref"):
                placeholder_count += 1
    print(f"Placeholder requests = 0: {'PASS' if placeholder_count == 0 else 'FAIL'} ({placeholder_count})")

    # Save formal result
    out = Path("experiment_planning_formal_v1.json")
    out.write_text(json.dumps(deep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nFormal result saved to {out.name}")
