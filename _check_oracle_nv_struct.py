"""Check ORACLE_NOT_VIOLATED execution result structure."""
import json

d = json.load(open('_scan_result.json', encoding='utf-8'))
v12 = d.get('v12', {})
exec_data = v12.get('experiment_execution', {})
results = exec_data.get('results', [])

# Find first ORACLE_NOT_VIOLATED
for r in results:
    if r.get('reason_code') == 'ORACLE_NOT_VIOLATED':
        print("Keys:", sorted(r.keys()))
        print(f"\nstatus: {r.get('status')}")
        print(f"reason_code: {r.get('reason_code')}")
        
        # Check oracle-related fields
        for key in ['oracle_status', 'oracle_receipt', 'oracle_receipt_id', 'contract_oracle']:
            val = r.get(key)
            if val:
                print(f"\n{key}: {json.dumps(val, indent=2)[:300] if isinstance(val, dict) else val}")
        
        # Check gate result
        gate = r.get('gate_result', {})
        if gate:
            print(f"\ngate_result: {json.dumps(gate, indent=2)[:300]}")
        
        # Check delivery gate
        delivery = r.get('delivery_gate', {})
        if delivery:
            print(f"\ndelivery_gate: {json.dumps(delivery, indent=2)[:300]}")
        
        break
