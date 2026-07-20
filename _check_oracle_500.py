"""Check oracle_verdict for ORACLE_NOT_VIOLATED."""
import json

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Find ORACLE_NOT_VIOLATED with HTTP 500
count_500 = 0
for r in results:
    if r.get('reason_code') != 'ORACLE_NOT_VIOLATED':
        continue
    
    # Check treatment response
    contracts = r.get('contract_evidence_receipts', [])
    has_500 = False
    for c in contracts:
        if isinstance(c, dict) and c.get('kind') == 'treatment':
            evidence = c.get('evidence', {})
            if isinstance(evidence, dict) and evidence.get('status_code') == 500:
                has_500 = True
                break
    
    if has_500:
        count_500 += 1
        if count_500 <= 3:
            oid = r.get('obligation_id', '')[:30]
            verdict = r.get('oracle_verdict', {})
            print(f"\n{oid}:")
            print(f"  oracle_verdict: {json.dumps(verdict, indent=2)[:400] if isinstance(verdict, dict) else verdict}")
            
            # Check treatment evidence
            for c in contracts:
                if isinstance(c, dict) and c.get('kind') == 'treatment':
                    evidence = c.get('evidence', {})
                    print(f"  treatment: {json.dumps(evidence, indent=2)[:200]}")

print(f"\nTotal ORACLE_NOT_VIOLATED with HTTP 500: {count_500}")
