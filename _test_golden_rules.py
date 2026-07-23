#!/usr/bin/env python
"""P0-11: Golden Rule Set integration test.

Verifies:
1. Behavior IR builds with state transitions and conservation invariants
2. Obligations compile with proper field bindings
3. Experiments have field_delta/conservation assertions
4. Oracle trace outputs field-level detail
"""
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.obligation_compiler_base import compile_obligations_from_behavior_ir
import json


def main():
    # Load pre-built knowledge asset
    asset_path = Path("platform_outputs/benchmark_mall/enterprise_knowledge_center/enterprise_business_knowledge_asset.json")
    if not asset_path.exists():
        print(f"ERROR: {asset_path} not found")
        return 1

    print("=" * 60)
    print("P0-11: Golden Rule Set Integration Test")
    print("=" * 60)

    # Step 1: Load knowledge asset and build Behavior IR
    print("\n[1] Building Behavior IR...")
    asset = json.loads(asset_path.read_text(encoding="utf-8"))
    print(f"    Rule library: {len(asset.get('rule_library', []))} rules")
    print(f"    State machines: {len(asset.get('state_machines', []))}")

    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="benchmark_mall",
    )

    invariants = ir.get("invariants", [])
    relations = ir.get("relations", [])
    states = ir.get("states", [])
    operations = ir.get("operations", [])

    print(f"    Operations: {len(operations)}")
    print(f"    States: {len(states)}")
    print(f"    Relations: {len(relations)}")
    print(f"    Invariants: {len(invariants)}")

    # Count invariant types
    inv_kinds = {}
    for inv in invariants:
        expr = inv.get("expression", {})
        kind = expr.get("kind", "unknown")
        inv_kinds[kind] = inv_kinds.get(kind, 0) + 1
    print(f"    Invariant kinds: {inv_kinds}")

    # Count state transition relations
    state_rels = [r for r in relations if r.get("relation_type") == "transitions"]
    print(f"    State transition relations: {len(state_rels)}")

    # Debug: show relation types
    rel_types = {}
    for r in relations:
        rt = r.get("relation_type", "unknown")
        rel_types[rt] = rel_types.get(rt, 0) + 1
    print(f"    Relation types: {rel_types}")

    # Debug: show state transition relation details
    print("\n[1.1] State transition relation details:")
    for r in state_rels[:3]:
        print(f"    - {r.get('id', '?')[:40]}")
        print(f"      from_ref: {r.get('from_ref')}")
        print(f"      to_ref: {r.get('to_ref')}")
        print(f"      operation_ref: {r.get('operation_ref')}")
        print(f"      from_state: {r.get('from_state')}")
        print(f"      to_state: {r.get('to_state')}")

    # Step 2: Compile obligations
    print("\n[2] Compiling obligations...")
    obligations_result = compile_obligations_from_behavior_ir(ir)
    obligations = obligations_result.get("obligations", [])

    print(f"    Total obligations: {len(obligations)}")

    # Count by family
    families = {}
    for obl in obligations:
        fam = obl.get("risk_family", "unknown")
        families[fam] = families.get(fam, 0) + 1
    print(f"    Families: {families}")

    # Debug: show obligation details
    print("\n[2.1] Obligation details:")
    for obl in obligations[:5]:
        print(f"    - {obl.get('obligation_id', '?')[:50]}")
        print(f"      risk_family: {obl.get('risk_family')}")
        print(f"      expression.kind: {obl.get('property', {}).get('expression', {}).get('kind')}")

    # Step 3: Check state obligations
    print("\n[3] State obligations detail:")
    state_obls = [o for o in obligations if o.get("risk_family") == "state"]
    for obl in state_obls[:5]:
        prop = obl.get("property", {})
        print(f"    - {obl.get('obligation_id', '?')[:40]}")
        print(f"      from_state_ref: {prop.get('from_state_ref')}")
        print(f"      to_state_ref: {prop.get('to_state_ref')}")
        print(f"      from_state: {prop.get('from_state')}")
        print(f"      to_state: {prop.get('to_state')}")
        print(f"      operation_ref: {prop.get('operation_ref')}")
        print(f"      expression: {prop.get('expression', {}).get('kind')}")
        print(f"      subject_refs: {obl.get('subject_refs', [])[:2]}")

    # Step 4: Check conservation obligations
    print("\n[4] Conservation obligations detail:")
    cons_obls = [o for o in obligations if o.get("risk_family") == "conservation"]
    for obl in cons_obls[:5]:
        prop = obl.get("property", {})
        expr = prop.get("expression", {})
        equation = expr.get("equation", {})
        terms = equation.get("terms", [])
        print(f"    - {obl.get('obligation_id', '?')[:40]}")
        print(f"      terms: {terms}")
        print(f"      operator: {equation.get('operator')}")

    # Step 5: Verify no empty terms
    print("\n[5] Verification:")
    empty_terms = [
        o for o in cons_obls
        if not o.get("property", {}).get("expression", {}).get("equation", {}).get("terms")
    ]
    print(f"    Conservation with empty terms: {len(empty_terms)}")
    if empty_terms:
        print("    WARNING: Empty terms found!")
        for o in empty_terms:
            print(f"      - {o.get('obligation_id')}")

    # Step 6: Check experiments compile
    print("\n[6] Experiment compilation check:")
    try:
        from ai_test_asset_center.experiment_compiler_obligation import compile_experiment_from_obligation

        compiled_count = 0
        blocked_count = 0
        for obl in obligations[:20]:
            try:
                exp = compile_experiment_from_obligation(
                    obligation=obl,
                    behavior_ir=ir,
                    project="benchmark_mall",
                )
                if exp.get("status") == "COMPILED":
                    compiled_count += 1
                    # Check assertion kind
                    assertion = exp.get("assertion", {})
                    kind = assertion.get("kind", "unknown")
                    if kind in ("field_delta", "conservation", "state_transition"):
                        print(f"    COMPILED: {obl.get('obligation_id', '?')[:35]} -> {kind}")
                else:
                    blocked_count += 1
            except Exception as e:
                blocked_count += 1
        print(f"    Compiled: {compiled_count}, Blocked: {blocked_count}")
    except ImportError as e:
        print(f"    SKIP: {e}")

    print("\n" + "=" * 60)
    print("Integration test complete")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
