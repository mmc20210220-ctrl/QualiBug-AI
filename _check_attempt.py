"""Check conservation attempt execution details."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check obligation_attempt_ledger for conservation
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]

print(f"Conservation attempts: {len(cons_attempts)}")

# Check the first conservation attempt in detail
if cons_attempts:
    a = cons_attempts[0]
    print(f"\nFirst conservation attempt keys: {list(a.keys())}")
    
    # Check for execution receipt
    exec_receipt = a.get("execution_receipt", {})
    print(f"\nexecution_receipt: {bool(exec_receipt)}")
    if exec_receipt:
        print(f"  keys: {list(exec_receipt.keys())[:15]}")
        print(f"  status: {exec_receipt.get('status')}")
        print(f"  reason_code: {exec_receipt.get('reason_code')}")
        
        # Check observations
        obs = exec_receipt.get("observations", {})
        print(f"\n  observations keys: {list(obs.keys())[:20]}")
        
        # Check multi_entity_state
        mes = obs.get("multi_entity_state", {})
        print(f"\n  multi_entity_state keys: {list(mes.keys())}")
        for key in list(mes.keys())[:5]:
            val = mes[key]
            if isinstance(val, dict):
                print(f"    {key}: before={type(val.get('before')).__name__}, after={type(val.get('after')).__name__}")
                if val.get("records"):
                    print(f"      records count: {len(val.get('records', []))}")
        
        # Check related_entity_observations
        rel_obs = obs.get("related_entity_observations", [])
        print(f"\n  related_entity_observations: {len(rel_obs)}")
        for ro in rel_obs[:2]:
            print(f"    - {ro.get('entity_alias')}: status={ro.get('status')}, records={len(ro.get('records', []))}")
        
        # Check related_entity_blockers
        rel_blockers = obs.get("related_entity_blockers", [])
        print(f"\n  related_entity_blockers: {len(rel_blockers)}")
        for b in rel_blockers[:3]:
            print(f"    - {b.get('entity_alias', b.get('entity_name'))}: {b.get('reason')}")
