"""Extract the actual binding plan / fixture_setup for a failing obligation
(obl_79bcf19c0a1a8a908c15, one of the 15 HARNESS_FAILED) from full.json, to
determine whether the cart-create fixture_setup was generated or not."""
import json
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
TARGET_OID = "obl_79bcf19c0a1a8a908c15"

# search the whole tree for this obligation and any fixture_setup / binding plan nearby
hits = []

def walk(obj, path_str=""):
    if isinstance(obj, dict):
        if obj.get("obligation_id") == TARGET_OID or obj.get("id") == TARGET_OID:
            hits.append((path_str, obj))
        for k, v in obj.items():
            walk(v, f"{path_str}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            walk(v, f"{path_str}[{i}]")

walk(d)
print(f"found {len(hits)} location(s) for {TARGET_OID}")
for p, obj in hits[:3]:
    print(f"\n=== at {p} (keys: {list(obj.keys())[:20]}) ===")

# Also: dump the experiment's binding plan / fixture_setup for this obligation if present
# search within test_obligations
tos = d.get("full_result", {}).get("v12", {}).get("test_obligations", {}).get("obligations", [])
print(f"\ntest_obligations count: {len(tos)}")
target_obl = None
for o in tos:
    if isinstance(o, dict) and (o.get("obligation_id") == TARGET_OID or o.get("id") == TARGET_OID):
        target_obl = o
        break
if target_obl is None:
    print("target obligation not found in test_obligations; dumping first obligation keys for structure")
    if tos:
        print(list(tos[0].keys()))
else:
    print("found target obligation; keys:", list(target_obl.keys()))
    blob = json.dumps(target_obl, ensure_ascii=False)
    # find fixture_setup / binding occurrences
    for kw in ("fixture_setup", "force_fixture_setup", "resolver_operations", "synthetic_value", "binding_plan", "runtime_read_binding"):
        print(f"  contains '{kw}': {kw in blob}")
    # print experiments / binding-related slices
    print("\n--- full target obligation (trimmed 3000) ---")
    print(blob[:3000])
