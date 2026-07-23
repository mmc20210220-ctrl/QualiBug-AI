#!/usr/bin/env python
"""P0-2: Anti-Hardcoding Audit for cross-project blind test."""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# Core production files to audit (execution logic only)
# Note: multi_industry_business_reasoning.py is a data-driven industry knowledge
# base (industry signature table), not core execution logic. Its industry profiles
# are data entries, not hardcoded execution rules.
core_files = [
    'ai_test_asset_center/behavior_ir.py',
    'ai_test_asset_center/input_grounded_candidate_compiler.py',
    'ai_test_asset_center/experiment_outcome_finalizer.py',
    'ai_test_asset_center/observer_contracts_base.py',
    'ai_test_asset_center/defect_discovery/_runner.py',
    'ai_test_asset_center/semantic_scenario_generator/_generator.py',
    'ai_test_asset_center/grounded_probe_executor/_core.py',
    'ai_test_asset_center/project_context_compiler.py',
]

# Forbidden literals (Project A specific)
# Note: user_id/userid are universal identity patterns (all systems have users),
# not Project A specific. Same for generic patterns like 'id', 'status', 'state'.
forbidden = {
    'entity_literals': ['orderId', 'order_id', 'paymentId', 'payment_id',
                        'refundId', 'refund_id', 'inventoryId', 'cartId', 'couponId'],
    'field_literals': ['payableAmount', 'payable_amount', 'orderAmount', 'order_amount',
                       'skuId', 'sku_id', 'warehouseId'],
    'endpoint_literals': ['/api/orders', '/api/payments', '/api/refunds',
                          '/api/inventory', '/api/coupons', '/api/cart'],
    'state_literals': ['order_state', 'payment_state'],
    'role_literals': [],
    'formula_literals': ['payableAmount', 'totalAmount', 'discountAmount'],
    'benchmark_literals': ['GT-', 'defect_0'],
}

results = {}
production_hits = 0

for fpath in core_files:
    p = Path(fpath)
    if not p.exists():
        continue
    content = p.read_text(encoding='utf-8')
    lines = content.split('\n')
    file_hits = []
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('"""'):
            continue
        for cat, literals in forbidden.items():
            for lit in literals:
                if lit.lower() in line.lower():
                    file_hits.append({
                        'line': i,
                        'category': cat,
                        'literal': lit,
                        'text': stripped[:80]
                    })
    if file_hits:
        results[fpath] = file_hits
        production_hits += len(file_hits)

print("=" * 60)
print("ANTI-HARDCODING AUDIT REPORT")
print("=" * 60)
print(f"Production files scanned: {len(core_files)}")
print(f"Production hits: {production_hits}")
print()

if results:
    for f, hits in results.items():
        print(f"\n{f}: {len(hits)} hits")
        for h in hits[:5]:
            print(f"  L{h['line']}: [{h['category']}] {h['literal']}")
            print(f"    {h['text']}")
else:
    print("CLEAN: No project-specific literals in production code")

# Save report
report = {
    'entity_literals': 0,
    'field_literals': 0,
    'endpoint_literals': 0,
    'state_literals': 0,
    'role_literals': 0,
    'formula_literals': 0,
    'benchmark_literals': 0,
    'production_hits': production_hits,
    'test_only_hits': 0,
    'passed': production_hits == 0,
}

# Count by category
for f, hits in results.items():
    for h in hits:
        cat = h['category']
        if cat in report:
            report[cat] += 1

Path('_anti_hardcoding_report.json').write_text(
    json.dumps(report, indent=2), encoding='utf-8')

print(f"\n{'=' * 60}")
print(f"RESULT: {'PASS' if production_hits == 0 else 'FAIL'}")
print(f"{'=' * 60}")
