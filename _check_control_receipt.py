"""Check control receipt for blocked oracle experiments."""
import json

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Find first ORACLE_NOT_VIOLATED with HTTP 500
for r in results:
    if r.get('reason_code') != 'ORACLE_NOT_VIOLATED':
        continue
    
    contracts = r.get('contract_evidence_receipts', [])
    has_500 = any(
        isinstance(c, dict) and c.get('kind') == 'treatment'
        and isinstance(c.get('evidence'), dict)
        and c['evidence'].get('status_code') == 500
        for c in contracts
    )
    if not has_500:
        continue
    
    print(f"Obligation: {r.get('obligation_id', '')[:40]}")
    
    # Print all contract evidence receipts
    print(f"\nContract evidence receipts ({len(contracts)}):")
    for c in contracts:
        if isinstance(c, dict):
            kind = c.get('kind', '')
            subject = c.get('subject_id', '')
            status = c.get('status', '')
            evidence = c.get('evidence', {})
            print(f"  {kind}:{subject} = {status}")
            if isinstance(evidence, dict):
                # Print key evidence fields
                for key in ['status_code', 'method', 'path', 'reason_code', 'block_reasons']:
                    if key in evidence:
                        val = evidence[key]
                        print(f"    {key}: {str(val)[:80]}")
    break
