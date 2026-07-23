"""Check conservation attempt operational receipt."""
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
    
    # Check operational_receipt
    op_receipt = a.get("operational_receipt", {})
    print(f"\noperational_receipt: {bool(op_receipt)}")
    if op_receipt:
        print(f"  keys: {list(op_receipt.keys())[:20]}")
        print(f"  status: {op_receipt.get('status')}")
        print(f"  reason_code: {op_receipt.get('reason_code')}")
        
        # Check observations
        obs = op_receipt.get("observations", {})
        print(f"\n  observations keys: {list(obs.keys())[:25]}")
        
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
        
        # Check related_entity_trace
        rel_trace = obs.get("related_entity_trace", [])
        print(f"\n  related_entity_trace: {len(rel_trace)}")
        for t in rel_trace[:3]:
            print(f"    - {t.get('entity_alias')}: status={t.get('status')}, candidates={t.get('candidates_found')}")
