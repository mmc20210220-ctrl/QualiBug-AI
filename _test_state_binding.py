"""Quick test: verify state transition operation binding and causal postcondition invariants."""
import sys, json
sys.path.insert(0, ".")
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset

data = json.load(open(
    "platform_workspace/benchmark_mall/defect_discovery/enterprise_business_knowledge_asset.json",
    "r", encoding="utf-8"
))
ir = build_behavior_ir_from_knowledge_asset(data)

# Check transition relations
relations = [r for r in ir.get("relations", []) if r.get("relation_type") == "transitions"]
print(f"=== State Transition Relations: {len(relations)} ===")
for r in relations[:10]:
    print(f"  {r.get('from_ref','?')[-25:]} -> {r.get('to_ref','?')[-25:]} via {r.get('operation_ref','?')}")

# Check coverage gaps for state transitions
gaps = [g for g in ir.get("coverage_gaps", []) if "state_transition" in str(g.get("typed_fields", {}).get("gap_type", ""))]
print(f"\n=== State Transition Coverage Gaps: {len(gaps)} ===")
for g in gaps[:5]:
    tf = g.get("typed_fields", {})
    print(f"  {tf.get('from_state','?')} -> {tf.get('to_state','?')}: {tf.get('description','')[:60]}")

# Check postcondition invariants
invariants = ir.get("invariants", [])
pc_invariants = [i for i in invariants if i.get("expression", {}).get("kind") == "postcondition"]
print(f"\n=== Postcondition Invariants: {len(pc_invariants)} ===")
for inv in pc_invariants[:5]:
    expr = inv.get("expression", {})
    ops = expr.get("operands", [])
    print(f"  {inv.get('id','?')[-30:]}: {expr.get('operator','?')} fields={[o.get('field','') for o in ops if isinstance(o,dict)]}")

# Check conservation invariants with equation
cons_invariants = [i for i in invariants if i.get("expression", {}).get("equation")]
print(f"\n=== Conservation Invariants with Equation: {len(cons_invariants)} ===")
for inv in cons_invariants[:5]:
    eq = inv.get("expression", {}).get("equation", {})
    print(f"  {inv.get('id','?')[-30:]}: terms={eq.get('terms', [])}")

# Summary
print(f"\n=== Summary ===")
print(f"Total invariants: {len(invariants)}")
print(f"Total relations: {len(ir.get('relations', []))}")
print(f"Transition relations: {len(relations)}")
print(f"Postcondition invariants: {len(pc_invariants)}")
print(f"Conservation with equation: {len(cons_invariants)}")
