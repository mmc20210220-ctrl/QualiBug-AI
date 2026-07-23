"""Check conservation execution details."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check conservation attempt
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]

if cons_attempts:
    a = cons_attempts[0]
    print(f"Conservation attempt:")
    print(f"  obligation_id: {a.get('obligation_id')}")
    print(f"  experiment_id: {a.get('experiment_id')}")
    print(f"  execution_id: {a.get('execution_id')}")
    print(f"  observation_receipt_ids: {a.get('observation_receipt_ids')}")
    print(f"  oracle_receipt_id: {a.get('oracle_receipt_id')}")
    print(f"  oracle_reason_code: {a.get('oracle_reason_code')}")
    
    # Check all keys
    print(f"\n  All keys: {list(a.keys())}")
    
    # Check for execution_result or similar
    for key in ['execution_result', 'execution_receipt', 'result', 'observations', 'oracle_evaluation']:
        val = a.get(key)
        if val:
            print(f"\n  {key}: {type(val).__name__}")
            if isinstance(val, dict):
                print(f"    keys: {list(val.keys())[:15]}")

# Check oracle receipts
oracle_receipts = v12.get("oracle_receipts", [])
print(f"\noracle_receipts count: {len(oracle_receipts)}")
cons_oracle = [o for o in oracle_receipts if o.get("obligation_id") == cons_attempts[0].get("obligation_id")] if cons_attempts else []
print(f"Conservation oracle receipts: {len(cons_oracle)}")
if cons_oracle:
    o = cons_oracle[0]
    print(f"  status: {o.get('status')}")
    print(f"  reason_code: {o.get('reason_code')}")
    print(f"  keys: {list(o.keys())[:15]}")
    # Check assertion results
    assertions = o.get("assertion_results", [])
    print(f"  assertion_results: {len(assertions)}")
    for ar in assertions[:3]:
        print(f"    - kind={ar.get('kind')}, result={ar.get('result')}, reason={ar.get('reason')}")

# Check observation receipts
obs_receipts = v12.get("observation_receipts", [])
print(f"\nobservation_receipts count: {len(obs_receipts)}")
if cons_attempts:
    obs_ids = cons_attempts[0].get("observation_receipt_ids", [])
    print(f"Conservation observation_receipt_ids: {obs_ids}")
    cons_obs = [o for o in obs_receipts if o.get("receipt_id") in obs_ids]
    print(f"Found observation receipts: {len(cons_obs)}")
    for o in cons_obs[:2]:
        print(f"  - type={o.get('observer_type')}, status={o.get('status')}")
