"""Test: verify obligation compilation from state transitions and postconditions."""
import sys, json
sys.path.insert(0, ".")
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler_base import compile_obligations_from_behavior_ir

data = json.load(open(
    "platform_workspace/benchmark_mall/defect_discovery/enterprise_business_knowledge_asset.json",
    "r", encoding="utf-8"
))
ir = build_behavior_ir_from_knowledge_asset(data)

# Check postcondition invariants source
invariants = ir.get("invariants", [])
pc_invariants = [i for i in invariants if i.get("expression", {}).get("kind") == "postcondition"]
print(f"=== Postcondition Invariants Detail ({len(pc_invariants)}) ===")
for inv in pc_invariants[:3]:
    expr = inv.get("expression", {})
    print(f"  id: {inv.get('id','?')}")
    print(f"  operator: {expr.get('operator','?')}")
    print(f"  operands: {json.dumps(expr.get('operands', [])[:2], ensure_ascii=False)[:200]}")
    print(f"  raw: {expr.get('raw','')[:80]}")
    print(f"  op_refs: {inv.get('operation_refs', [])}")
    print()

# Compile obligations
result = compile_obligations_from_behavior_ir(ir)
obligations = result.get("obligations", [])
print(f"\n=== Obligation Summary ===")
print(f"Total obligations: {len(obligations)}")
by_family = result.get("by_family", {})
for fam, count in sorted(by_family.items(), key=lambda x: -x[1]):
    if count > 0:
        print(f"  {fam}: {count}")

# Check state obligations
state_obls = [o for o in obligations if o.get("risk_family") == "state"]
print(f"\n=== State Obligations: {len(state_obls)} ===")
for o in state_obls[:5]:
    prop = o.get("property", {})
    print(f"  {o.get('obligation_id','?')[-30:]}: from={prop.get('from_state_ref','?')[-20:]} to={prop.get('to_state_ref','?')[-20:]} op={prop.get('operation_ref','?')[-20:]}")

# Check conservation obligations
cons_obls = [o for o in obligations if o.get("risk_family") == "conservation"]
print(f"\n=== Conservation Obligations: {len(cons_obls)} ===")
for o in cons_obls[:3]:
    prop = o.get("property", {})
    expr = prop.get("expression", {})
    eq = expr.get("equation", {})
    print(f"  {o.get('obligation_id','?')[-30:]}: terms={eq.get('terms', [])} op={prop.get('operation_ref','?')[-20:]}")
