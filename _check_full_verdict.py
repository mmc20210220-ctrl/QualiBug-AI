"""Check full oracle activation receipt."""
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
    
    verdict = r.get('oracle_verdict', {})
    print("Full oracle_verdict:")
    print(json.dumps(verdict, indent=2)[:1500])
    break
