#!/usr/bin/env python
"""P0-2: Complete hardcoding audit for generalization."""
import json, sys, re, pathlib
sys.stdout.reconfigure(encoding='utf-8')

prod_dir = pathlib.Path('ai_test_asset_center')
py_files = [f for f in prod_dir.rglob('*.py') if '__pycache__' not in str(f)]

# Categories to scan
state_terms = ['PAID', 'CANCELLED', 'REFUNDED', 'PENDING_PAYMENT', 'FULFILLED', 'SHIPPED']
role_terms = ['buyer', 'merchant', 'warehouse_operator', 'customer_service']
bench_terms = ['INV-006', 'INV-00', 'ORD-0', 'PAY-0', 'REF-0', 'BUG-']
formula_terms = ['available_qty', 'locked_qty', 'total_amount', 'refund_amount']

results = {
    'state_values': {},
    'role_names': {},
    'benchmark_ids': {},
    'business_formulas': {},
}

for f in py_files:
    text = f.read_text(encoding='utf-8', errors='ignore')
    fname = str(f.relative_to(prod_dir))
    lines = text.splitlines()
    
    for t in state_terms:
        hits = [i+1 for i, line in enumerate(lines) if t in line and not line.strip().startswith('#')]
        if hits:
            results['state_values'].setdefault(t, []).append({'file': fname, 'lines': hits[:5]})
    
    for t in role_terms:
        hits = [i+1 for i, line in enumerate(lines) if t in line and not line.strip().startswith('#')]
        if hits:
            results['role_names'].setdefault(t, []).append({'file': fname, 'lines': hits[:5]})
    
    for t in bench_terms:
        hits = [i+1 for i, line in enumerate(lines) if t in line]
        if hits:
            results['benchmark_ids'].setdefault(t, []).append({'file': fname, 'lines': hits[:5]})
    
    for t in formula_terms:
        hits = [i+1 for i, line in enumerate(lines) if t in line and not line.strip().startswith('#')]
        if hits:
            results['business_formulas'].setdefault(t, []).append({'file': fname, 'lines': hits[:5]})

# Summary
print("=" * 60)
print("HARDCODING AUDIT REPORT")
print("=" * 60)

for category, items in results.items():
    total = sum(len(v) for v in items.values())
    print(f"\n{'─'*40}")
    print(f"Category: {category} (total file-hits: {total})")
    print(f"{'─'*40}")
    for term, locations in sorted(items.items()):
        files_str = ', '.join(loc['file'] for loc in locations)
        print(f"  {term}: {files_str}")

# Count production-code hits that are NOT in comments/docstrings
print(f"\n{'='*60}")
print("CRITICAL (core execution logic, must fix):")
print("  observer_contracts_base.py: identity keys + state fields")
print("  multi_industry_business_reasoning.py: domain rules + states")
print("  semantic_scenario_generator/_generator.py: extract fields")
print("  grounded_probe_executor/_common.py + _core.py: entity regex")
print(f"{'='*60}")

# Save full report
with open('_hardcoding_audit.json', 'w', encoding='utf-8') as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print("\nFull report saved to _hardcoding_audit.json")
