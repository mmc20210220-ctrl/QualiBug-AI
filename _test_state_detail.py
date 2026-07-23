"""Verify state nodes have names and experiment compilation works."""
import sys, json
sys.path.insert(0, ".")
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler_base import compile_obligations_from_behavior_ir

data = json.load(open(
    "platform_workspace/benchmark_mall/defect_discovery/enterprise_business_knowledge_asset.json",
    "r", encoding="utf-8"
))
ir = build_behavior_ir_from_knowledge_asset(data)

# Check state nodes
states = ir.get("states", [])
print(f"=== State Nodes: {len(states)} ===")
for s in states[:10]:
    print(f"  {s.get('id','?')[-25:]}: name={s.get('name','?')} entity={s.get('entity_ref','?')}")

# Check operations that transitions bind to
operations = ir.get("operations", [])
ops_by_id = {o.get("id"): o for o in operations if isinstance(o, dict)}
relations = [r for r in ir.get("relations", []) if r.get("relation_type") == "transitions"]
print(f"\n=== Transition Operations ===")
seen_ops = set()
for r in relations:
    op_id = r.get("operation_ref", "")
    if op_id and op_id not in seen_ops:
        seen_ops.add(op_id)
        op = ops_by_id.get(op_id, {})
        print(f"  {op_id[-25:]}: {op.get('method','?')} {op.get('path','?')[:50]}")

# Try compiling one state obligation into experiment
result = compile_obligations_from_behavior_ir(ir)
obligations = result.get("obligations", [])
state_obls = [o for o in obligations if o.get("risk_family") == "state"]
print(f"\n=== State Obligation Property Details (first 3) ===")
for o in state_obls[:3]:
    prop = o.get("property", {})
    print(f"  obl: {o.get('obligation_id','?')[-25:]}")
    print(f"    from_state_ref: {prop.get('from_state_ref','?')}")
    print(f"    to_state_ref: {prop.get('to_state_ref','?')}")
    print(f"    from_state: {prop.get('from_state','?')}")
    print(f"    to_state: {prop.get('to_state','?')}")
    print(f"    operation_ref: {prop.get('operation_ref','?')}")
    print(f"    template: {prop.get('template','?')}")
    print()
