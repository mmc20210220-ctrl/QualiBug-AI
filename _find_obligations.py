"""Find full obligation data with property_specs."""
import json
from pathlib import Path

result = json.loads(Path("platform_outputs/contractflow_project_c/scan_result.json").read_text(encoding="utf-8"))
v12 = result.get("v12", {})

# Check test_obligations
test_obls = v12.get("test_obligations", {})
print(f"test_obligations type: {type(test_obls)}")
if isinstance(test_obls, dict):
    print(f"test_obligations keys: {list(test_obls.keys())[:10]}")
    obls = test_obls.get("obligations", [])
    print(f"test_obligations.obligations: {len(obls)}")
    if obls:
        # Find conservation obligations
        cons = [o for o in obls if o.get("risk_family") == "conservation"]
        print(f"Conservation obligations: {len(cons)}")
        if cons:
            obl = cons[0]
            print(f"\nFirst conservation obligation:")
            print(f"  obligation_id: {obl.get('obligation_id')}")
            props = obl.get("property_specs", [])
            print(f"  property_specs: {len(props)}")
            if props:
                print(f"  First property_spec:")
                print(json.dumps(props[0], indent=4, ensure_ascii=False)[:800])
elif isinstance(test_obls, list):
    print(f"test_obligations list: {len(test_obls)}")
    if test_obls:
        cons = [o for o in test_obls if o.get("risk_family") == "conservation"]
        print(f"Conservation: {len(cons)}")
        if cons:
            print(json.dumps(cons[0], indent=2, ensure_ascii=False)[:1000])

# Also check experiment_compile
exp_compile = v12.get("experiment_compile", {})
print(f"\nexperiment_compile type: {type(exp_compile)}")
if isinstance(exp_compile, dict):
    print(f"experiment_compile keys: {list(exp_compile.keys())[:10]}")
