"""Check for experiment results in scan response."""
import json

d = json.load(open("_ecommerce_scan_result.json", "r", encoding="utf-8"))

# Check layers
layers = d.get("layers", {})
if isinstance(layers, dict):
    print("layers keys:", list(layers.keys())[:10])
    for k, v in layers.items():
        if isinstance(v, dict):
            print(f"  {k}: {list(v.keys())[:10]}")
        elif isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")

# Check for experiment data in various locations
for key in ["experiment_execution", "experiment_compile", "experiments", "obligations"]:
    val = d.get(key)
    if val:
        print(f"\n{key}: {type(val).__name__}")
        if isinstance(val, dict):
            print(f"  keys: {list(val.keys())[:10]}")
        elif isinstance(val, list):
            print(f"  count: {len(val)}")

# Check findings
findings = d.get("findings", [])
print(f"\nfindings: {len(findings)}")
for f in findings[:5]:
    print(f"  {f.get('title', '')[:60]}")

# Check candidate_findings
candidates = d.get("candidate_findings", [])
print(f"\ncandidate_findings: {len(candidates)}")

# Check risk_clues
clues = d.get("risk_clues", [])
print(f"\nrisk_clues: {len(clues)}")
