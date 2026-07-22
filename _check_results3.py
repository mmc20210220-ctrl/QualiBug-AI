"""Check oracle details and find evaluator."""
import json

with open("_scan_result_latest.json", "r", encoding="utf-8") as f:
    d = json.load(f)

findings = d.get("findings", [])

# Oracle details for first 3 findings
for i in range(min(3, len(findings))):
    f = findings[i]
    print(f"=== FINDING {i}: {f.get('title','')[:70]} ===")
    oracle = f.get("oracle") or {}
    print(f"  oracle keys: {sorted(oracle.keys())}")
    print(f"  oracle.status: {oracle.get('status')}")
    print(f"  oracle.verdict: {oracle.get('verdict')}")
    print(f"  oracle.property: {oracle.get('property')}")
    print(f"  oracle.assertion_kind: {oracle.get('assertion_kind')}")
    print(f"  oracle.control_status: {oracle.get('control_status')}")
    print(f"  oracle.treatment_status: {oracle.get('treatment_status')}")
    print(f"  expected: {f.get('expected')}")
    print(f"  actual: {f.get('actual')}")
    print(f"  control_succeeded: {(f.get('evidence') or {}).get('control_succeeded')}")
    print(f"  description: {f.get('description','')[:120]}")
    # Failed assertions
    fa = f.get("failed_assertions") or []
    for a in fa[:2]:
        if isinstance(a, dict):
            print(f"  failed_assertion: kind={a.get('kind')} expected={a.get('expected')} actual={a.get('actual')} status={a.get('status')}")
    print()

# Check v12 stats
v12 = d.get("v12") or {}
print("=== V12 KEY STATS ===")
for k in ["total_obligations", "selected_obligations", "executed_obligations", 
           "blocked_obligations", "delivered_defects", "findings_count",
           "contract_oracle_active", "contract_oracle_blocked", "harness_failed"]:
    print(f"  {k}: {v12.get(k)}")

# Discovery funnel
funnel = d.get("discovery_funnel") or {}
print("\n=== DISCOVERY FUNNEL ===")
for k, v in sorted(funnel.items()):
    if isinstance(v, (int, float, str)):
        print(f"  {k}: {v}")

# Pipeline health
ph = d.get("pipeline_health") or {}
print("\n=== PIPELINE HEALTH (key metrics) ===")
for k in sorted(ph.keys()):
    v = ph[k]
    if isinstance(v, (int, float, str, bool)):
        print(f"  {k}: {v}")
