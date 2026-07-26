"""Check source registry and API operations available for benchmark_mall_131."""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

# Check source registry
reg_path = Path("platform_workspace/benchmark_mall_131/enterprise_knowledge_center/source_registry.json")
with open(reg_path, "r", encoding="utf-8") as f:
    reg = json.load(f)

print("=== Source Registry ===")
for src in reg.get("sources", []):
    print(f"  {src.get('source_id','')}: {src.get('filename','')} type={src.get('source_type','')} size={src.get('size',0)}")

# Check input files
print("\n=== Input Files ===")
input_dir = Path("platform_inputs/benchmark_mall_131")
if input_dir.exists():
    for f in sorted(input_dir.rglob("*")):
        if f.is_file():
            print(f"  {f.relative_to(input_dir)} ({f.stat().st_size} bytes)")

# Check workspace input
ws_input = Path("platform_workspace/benchmark_mall_131/input")
if ws_input.exists():
    print(f"\n=== Workspace Input ===")
    for f in sorted(ws_input.rglob("*")):
        if f.is_file():
            print(f"  {f.relative_to(ws_input)} ({f.stat().st_size} bytes)")

# Load knowledge asset interfaces
from ai_test_asset_center.enterprise_knowledge_center import load_enterprise_business_knowledge_asset
asset = load_enterprise_business_knowledge_asset("benchmark_mall_131", root=Path("."))
if asset:
    interfaces = asset.get("interfaces", [])
    print(f"\n=== Knowledge Asset Interfaces: {len(interfaces)} ===")
    for iface in interfaces[:30]:
        method = iface.get("method", iface.get("http_method", ""))
        path = iface.get("path", iface.get("endpoint", ""))
        print(f"  {method} {path}")

# Check how the scan loads API operations
print("\n=== Checking API operation loading ===")
from ai_test_asset_center.private_pilot_scan_prep import prepare_scan_context
print("prepare_scan_context available")
