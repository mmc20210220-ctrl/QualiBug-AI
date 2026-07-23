"""Check scan result structure."""
import json
from pathlib import Path

result = json.loads(Path("platform_outputs/contractflow_project_c/scan_result.json").read_text(encoding="utf-8"))

print("=== Top-level keys ===")
for k in sorted(result.keys()):
    v = result[k]
    if isinstance(v, dict):
        print(f"  {k}: dict({len(v)} keys)")
    elif isinstance(v, list):
        print(f"  {k}: list({len(v)} items)")
    elif isinstance(v, str) and len(v) > 100:
        print(f"  {k}: str({len(v)} chars)")
    else:
        print(f"  {k}: {v}")

# Check where behavior_ir lives
print("\n=== Searching for behavior_ir ===")
def find_key(d, target, path=""):
    if isinstance(d, dict):
        for k, v in d.items():
            if k == target:
                if isinstance(v, dict):
                    print(f"  Found at {path}.{k}: dict({len(v)} keys: {list(v.keys())[:8]})")
                elif isinstance(v, list):
                    print(f"  Found at {path}.{k}: list({len(v)} items)")
                else:
                    print(f"  Found at {path}.{k}: {type(v).__name__}")
            if isinstance(v, dict) and path.count(".") < 3:
                find_key(v, target, f"{path}.{k}")
            elif isinstance(v, list) and len(v) > 0 and isinstance(v[0], dict) and path.count(".") < 2:
                find_key(v[0], target, f"{path}.{k}[0]")

find_key(result, "behavior_ir")
find_key(result, "obligation_attempt_ledger")
find_key(result, "runtime_contract")
find_key(result, "preflight_receipt")

# Check the execution result structure
exec_result = result.get("execution_result", {})
if exec_result:
    print(f"\n=== execution_result keys ===")
    for k in sorted(exec_result.keys())[:20]:
        v = exec_result[k]
        if isinstance(v, dict):
            print(f"  {k}: dict({len(v)} keys)")
        elif isinstance(v, list):
            print(f"  {k}: list({len(v)} items)")
        else:
            print(f"  {k}: {repr(v)[:80]}")
