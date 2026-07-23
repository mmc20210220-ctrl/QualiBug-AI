"""Inspect rule_library conservation/causal/state rules."""
import json

d = json.load(open('platform_workspace/benchmark_mall/defect_discovery/enterprise_business_knowledge_asset.json', 'r', encoding='utf-8'))
rl = d.get('rule_library') or []

print(f"Total rules: {len(rl)}")
print(f"\nRule types: {dict(sorted({r.get('rule_type','?'): sum(1 for x in rl if x.get('rule_type')==r.get('rule_type')) for r in rl}.items()))}")

print("\n=== CONSERVATION / CAUSAL / STATE RULES ===")
for r in rl:
    rt = r.get('rule_type', '')
    risk = r.get('risk_type', '')
    if rt in ('conservation', 'causal', 'state_transition') or 'causal' in risk or 'conserv' in risk:
        print(f"\n[{rt}/{risk}] {r.get('rule_id','')}")
        stmt = r.get('statement', '')
        print(f"  statement: {stmt[:150]}")
        cc = r.get('causal_chain') or {}
        if cc:
            print(f"  trigger: {cc.get('trigger_action','')}")
            for pc in (cc.get('postconditions') or [])[:4]:
                print(f"    pc: entity={pc.get('entity','')} field={pc.get('field','')} must_become={pc.get('must_become','')} must_create={pc.get('must_create','')}")
        # Check for structured expression
        expr = r.get('expression') or {}
        if expr:
            print(f"  expression: {json.dumps(expr, ensure_ascii=False)[:200]}")

# Also show business_objects
bo = d.get('business_objects') or []
print(f"\n\n=== BUSINESS OBJECTS ({len(bo)}) ===")
for obj in bo[:10]:
    if not isinstance(obj, dict):
        continue
    name = obj.get('name') or obj.get('object') or ''
    fields = obj.get('fields') or obj.get('attributes') or []
    print(f"  {name}: {len(fields)} fields")
    if fields and isinstance(fields[0], dict):
        for f in fields[:5]:
            print(f"    - {f.get('name','')}: {f.get('type','')} {f.get('description','')[:60]}")
    elif fields and isinstance(fields[0], str):
        for f in fields[:8]:
            print(f"    - {f}")
