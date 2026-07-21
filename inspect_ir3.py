# -*- coding: utf-8 -*-
"""Full IR invariant kinds and state enumeration."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

path = r"d:\QualiBug-AI\QualiBug-AI-main\platform_outputs\benchmark_mall\scan_result.json"
with open(path, 'r', encoding='utf-8') as f:
    result = json.load(f)
bir = result.get('v12', result).get('behavior_ir', {})

# All invariant expression kinds
invariants = bir.get('invariants', [])
from collections import Counter
kinds = Counter()
for inv in invariants:
    expr = inv.get('expression', {})
    if isinstance(expr, dict):
        kinds[expr.get('kind', '')] += 1
    else:
        kinds['non-dict'] += 1
print(f"Invariant expression kinds: {dict(kinds)}")

# Non-forbidden_state_transition invariants
for inv in invariants:
    expr = inv.get('expression', {})
    if isinstance(expr, dict) and expr.get('kind') != 'forbidden_state_transition':
        print(f"\n  [{expr.get('kind','')}] {inv.get('description','')[:120]}")
        print(f"    expr: {json.dumps(expr, ensure_ascii=False)[:250]}")

# All states grouped by entity
states = bir.get('states', [])
entity_states = {}
for s in states:
    ent = s.get('entity_ref', '')
    name = s.get('name', '')
    entity_states.setdefault(ent, []).append(name)
print(f"\n=== States by entity ===")
for ent, vals in sorted(entity_states.items()):
    print(f"  {ent}: {vals}")

# All entities
entities = bir.get('entities', [])
print(f"\n=== Entities ({len(entities)}) ===")
for e in entities:
    print(f"  {e.get('name','')} (kind={e.get('kind','')})")

# Relations
relations = bir.get('relations', [])
print(f"\n=== Relations ({len(relations)}) ===")
for r in relations[:10]:
    print(f"  {r.get('from_entity','')} --[{r.get('kind','')}]--> {r.get('to_entity','')} | {r.get('description','')[:80]}")
