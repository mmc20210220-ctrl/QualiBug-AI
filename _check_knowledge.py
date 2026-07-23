"""Check business rules and knowledge extraction."""
import json

d = json.load(open("_scan_result_project_b.json", encoding="utf-8"))

# Check all keys for rule-related content
print("=== Searching for rules/knowledge ===")
for key in sorted(d.keys()):
    val = d[key]
    if isinstance(val, dict):
        # Check nested keys
        for subkey in val.keys():
            if "rule" in subkey.lower() or "knowledge" in subkey.lower():
                subval = val[subkey]
                if isinstance(subval, list):
                    print(f"{key}.{subkey}: {len(subval)} items")
                elif isinstance(subval, dict):
                    print(f"{key}.{subkey}: {len(subval)} keys")
    elif isinstance(val, list) and len(val) > 0:
        if "rule" in key.lower() or "knowledge" in key.lower():
            print(f"{key}: {len(val)} items")

# Check evidence_bundle
eb = d.get("evidence_bundle", {})
if eb:
    print(f"\n=== Evidence Bundle ===")
    print(f"Keys: {list(eb.keys())[:15]}")

# Check discovery_evolution
de = d.get("discovery_evolution", {})
if de:
    print(f"\n=== Discovery Evolution ===")
    for k, v in de.items():
        if isinstance(v, (int, float, str, bool)):
            print(f"  {k}: {v}")

# Check commercial_readiness
cr = d.get("commercial_readiness", {})
if cr:
    print(f"\n=== Commercial Readiness ===")
    for k, v in cr.items():
        if isinstance(v, (int, float, str, bool)):
            print(f"  {k}: {v}")

# Check benchmark_metrics
bm = d.get("benchmark_metrics", {})
if bm:
    print(f"\n=== Benchmark Metrics ===")
    for k, v in bm.items():
        if isinstance(v, (int, float, str, bool)):
            print(f"  {k}: {v}")
