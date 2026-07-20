"""Check oracle activation blockers for HTTP 500 experiments."""
import json
from collections import Counter

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Find ORACLE_NOT_VIOLATED with HTTP 500
blocker_counts = Counter()
sample_blockers = []

for r in results:
    if r.get('reason_code') != 'ORACLE_NOT_VIOLATED':
        continue
    
    # Check for HTTP 500
    contracts = r.get('contract_evidence_receipts', [])
    has_500 = any(
        isinstance(c, dict) and c.get('kind') == 'treatment'
        and isinstance(c.get('evidence'), dict)
        and c['evidence'].get('status_code') == 500
        for c in contracts
    )
    if not has_500:
        continue
    
    # Get oracle verdict activation receipt
    verdict = r.get('oracle_verdict', {})
    if isinstance(verdict, dict):
        activation = verdict.get('activation_receipt', {})
        if isinstance(activation, dict):
            blockers = activation.get('blockers', [])
            harness_failures = activation.get('harness_failures', [])
            for b in blockers:
                blocker_counts[f"BLOCKER: {b[:60]}"] += 1
            for h in harness_failures:
                blocker_counts[f"HARNESS: {h[:60]}"] += 1
            
            if len(sample_blockers) < 3:
                sample_blockers.append({
                    'oid': r.get('obligation_id', '')[:30],
                    'blockers': blockers[:5],
                    'harness_failures': harness_failures[:5],
                })

print("Oracle activation blockers for HTTP 500 experiments:")
for b, c in blocker_counts.most_common(20):
    print(f"  {b}: {c}")

print("\nSample blockers:")
for s in sample_blockers:
    print(f"\n  {s['oid']}:")
    print(f"    blockers: {s['blockers']}")
    print(f"    harness_failures: {s['harness_failures']}")
