"""Check conservation execution observations."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_execution results
exp_exec = v12.get("experiment_execution", {})
results = exp_exec.get("results", [])

# Find conservation result
cons_results = [r for r in results if r.get("obligation_id") == "obl_0413ca640355cba0d746"]

if cons_results:
    res = cons_results[0]
    
    # Check all keys
    print(f"Result keys: {list(res.keys())}")
    
    # Check for observations
    obs = res.get("observations", {})
    print(f"\nobservations: {bool(obs)}")
    if obs:
        print(f"  keys: {list(obs.keys())}")
        
        # Check multi_entity_state
        mes = obs.get("multi_entity_state", {})
        print(f"\n  multi_entity_state keys: {list(mes.keys())}")
        for key in list(mes.keys())[:5]:
            val = mes[key]
            if isinstance(val, dict):
                print(f"    {key}: before={type(val.get('before')).__name__}, after={type(val.get('after')).__name__}")
                if isinstance(val.get("after"), list):
                    print(f"      after count: {len(val.get('after', []))}")
                elif isinstance(val.get("after"), dict):
                    print(f"      after keys: {list(val.get('after', {}).keys())[:10]}")
        
        # Check related_entity_observations
        rel_obs = obs.get("related_entity_observations", [])
        print(f"\n  related_entity_observations: {len(rel_obs)}")
        for ro in rel_obs[:3]:
            print(f"    - {ro.get('entity_alias')}: status={ro.get('status')}, records={len(ro.get('records', []))}")
        
        # Check related_entity_multi_state
        rel_mes = obs.get("related_entity_multi_state", {})
        print(f"\n  related_entity_multi_state keys: {list(rel_mes.keys())}")
        
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
    
    # Check observer_receipts
    observer_receipts = res.get("observer_receipts", [])
    print(f"\nobserver_receipts: {len(observer_receipts)}")
    for i, obs_r in enumerate(observer_receipts[:5]):
        print(f"  [{i}] keys={list(obs_r.keys())[:10]}")
        print(f"      observer_type={obs_r.get('observer_type')}, status={obs_r.get('status')}")
