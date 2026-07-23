"""Check source_grounded_discovery layer details."""
import json

d = json.load(open("_ecommerce_scan_result.json", "r", encoding="utf-8"))

layers = d.get("layers", {})
sgd = layers.get("source_grounded_discovery", {})
print("source_grounded_discovery:")
print(f"  execution_status: {sgd.get('execution_status')}")
print(f"  campaign_id: {sgd.get('campaign_id')}")
print(f"  ms: {sgd.get('ms')}")
print(f"  findings: {len(sgd.get('findings', []))}")
print(f"  candidates: {len(sgd.get('candidates', []))}")

# Check for experiment data in the layer
for key in ["experiment_execution", "experiment_compile", "experiments", "obligations", "agent_intent_plan"]:
    val = sgd.get(key)
    if val:
        print(f"  {key}: {type(val).__name__}")
        if isinstance(val, dict):
            print(f"    keys: {list(val.keys())[:10]}")
        elif isinstance(val, list):
            print(f"    count: {len(val)}")

# Check all keys in sgd
print(f"\nAll keys in source_grounded_discovery: {list(sgd.keys())}")
