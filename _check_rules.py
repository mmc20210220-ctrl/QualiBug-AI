"""Check rule generation from Project B scan."""
import json

d = json.load(open("_scan_result_project_b.json", "r", encoding="utf-8"))

# Check behavior IR / knowledge asset
print("=== Rule Generation Check ===")

# Check behavior_slices
slices = d.get("behavior_slices", [])
print(f"Behavior slices: {len(slices)}")

# Check knowledge asset
ka = d.get("knowledge_asset", {})
if ka:
    rules = ka.get("business_rules", [])
    print(f"Business rules in knowledge_asset: {len(rules)}")
    for i, r in enumerate(rules[:10]):
        print(f"  [{i+1}] {r.get('rule_id', '?')}: {r.get('description', '?')[:60]}")

# Check behavior_ir
bir = d.get("behavior_ir", {})
if bir:
    nodes = bir.get("nodes", [])
    edges = bir.get("edges", [])
    print(f"\nBehavior IR: {len(nodes)} nodes, {len(edges)} edges")

# Check obligations
obls = d.get("obligations", [])
print(f"\nObligations compiled: {len(obls)}")

# Check experiment count
exps = d.get("experiments", [])
print(f"Experiments compiled: {len(exps)}")

# Check source knowledge overlay
overlay = d.get("source_knowledge_overlay", {})
if overlay:
    print(f"\nSource knowledge overlay keys: {list(overlay.keys())[:10]}")

# Check any rules in the result
for key in d.keys():
    if "rule" in key.lower():
        val = d.get(key)
        if isinstance(val, list):
            print(f"\n{key}: {len(val)} items")
        elif isinstance(val, dict):
            print(f"\n{key}: {len(val)} keys")
