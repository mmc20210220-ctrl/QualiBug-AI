"""Test the readback resolver against actual benchmark Behavior IR."""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center import load_enterprise_business_knowledge_asset
from ai_test_asset_center.source_declared_readback_resolver import (
    resolve_readback_contract,
    resolve_readback_for_obligations,
    STATUS_RESOLVED,
)

# Load knowledge asset and build Behavior IR
asset = load_enterprise_business_knowledge_asset("benchmark_mall_131", root=Path("."))
behavior_ir = build_behavior_ir_from_knowledge_asset(
    asset,
    project_id="benchmark_mall_131",
)

operations = behavior_ir.get("operations", [])
print(f"Behavior IR operations: {len(operations)}")

# Test each write operation
write_ops = [
    op for op in operations
    if isinstance(op, dict) and (op.get("method") or "").upper() in ("POST", "PUT", "PATCH", "DELETE")
]
print(f"Write operations: {len(write_ops)}")
print()

resolved = 0
blocked = 0
for op in write_ops:
    result = resolve_readback_contract(op, behavior_ir=behavior_ir)
    method = (op.get("method") or "").upper()
    path = op.get("path", "")
    status = result["status"]
    contract_id = ""
    if result.get("contract"):
        contract_id = result["contract"].get("contract_id", "")
        surface = result["contract"].get("readback_surface_type", "")
        identity = result["contract"].get("identity_strategy", {}).get("type", "")
        read_path = result["contract"].get("endpoint_template", "")
        print(f"  RESOLVED {method} {path}")
        print(f"    -> {surface} via {read_path} identity={identity}")
        resolved += 1
    else:
        reason = result.get("block_reason", "")
        print(f"  BLOCKED  {method} {path} -> {reason} (candidates={result.get('candidates_analyzed',0)})")
        blocked += 1

print(f"\n=== Summary ===")
print(f"Resolved: {resolved}/{len(write_ops)}")
print(f"Blocked:  {blocked}/{len(write_ops)}")

# Now test against the 138 affected obligations
print(f"\n=== Testing against 138 affected obligations ===")
with open("artifacts/spec_v1_2_3/v123_readback_baseline.json", "r", encoding="utf-8") as f:
    baseline = json.load(f)

ledger = resolve_readback_for_obligations(
    baseline["candidates"],
    behavior_ir=behavior_ir,
)
print(f"Total: {ledger['total_obligations']}")
print(f"Resolved: {ledger['resolved_count']}")
print(f"Blocked: {ledger['blocked_count']}")
print(f"Resolution rate: {ledger['resolution_rate']:.1%}")

# Show block reasons
from collections import Counter
block_reasons = Counter(
    r["block_reason"] for r in ledger["results"] if r["readback_status"] == "BLOCKED"
)
print(f"\nBlock reasons:")
for reason, cnt in block_reasons.most_common():
    print(f"  {reason}: {cnt}")
