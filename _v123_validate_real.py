"""V1.2.3 resolver validation against real benchmark knowledge asset."""
import json
from pathlib import Path

from ai_test_asset_center.source_declared_readback_resolver import (
    resolve_readback_for_obligations,
    resolve_readback_contract,
    STATUS_RESOLVED,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center import (
    load_enterprise_business_knowledge_asset,
)

asset = load_enterprise_business_knowledge_asset("benchmark_mall_131", root=Path("."))
ir = build_behavior_ir_from_knowledge_asset(
    asset,
    project_id="benchmark_mall_131",
)

ops = ir.get("operations", [])
writes = [
    op for op in ops
    if isinstance(op, dict) and str(op.get("read_write", "")).lower() == "write"
]
print(f"Total IR operations: {len(ops)}")
print(f"Write operations: {len(writes)}")
print()

# Resolve per write operation
resolved_ops = []
blocked_ops = []
for op in writes:
    result = resolve_readback_contract(op, behavior_ir=ir)
    op_id = str(op.get("id", ""))
    method = str(op.get("method", ""))
    path = str(op.get("path", ""))
    if result["status"] == STATUS_RESOLVED:
        contract = result["contract"]
        resolved_ops.append({
            "op_id": op_id,
            "method": method,
            "path": path,
            "surface": contract.get("readback_surface_type", ""),
            "read_op": contract.get("read_operation_id", ""),
            "identity": contract.get("identity_strategy", {}).get("type", ""),
        })
    else:
        blocked_ops.append({
            "op_id": op_id,
            "method": method,
            "path": path,
            "reason": result.get("block_reason", ""),
        })

print(f"=== RESOLVED: {len(resolved_ops)}/{len(writes)} ===")
for r in resolved_ops:
    print(f"  {r['method']:6s} {r['path']:45s} -> {r['surface']:30s} via {r['read_op']}")

print(f"\n=== BLOCKED: {len(blocked_ops)}/{len(writes)} ===")
for b in blocked_ops:
    print(f"  {b['method']:6s} {b['path']:45s} -> {b['reason']}")

# Batch obligation resolution
obligations = [
    {"obligation_id": f"obl_{i}", "operation_refs": [str(op.get("id", ""))]}
    for i, op in enumerate(writes)
]
ledger = resolve_readback_for_obligations(obligations, behavior_ir=ir)
print(f"\n=== BATCH LEDGER ===")
print(f"Total: {ledger['total_obligations']}")
print(f"Resolved: {ledger['resolved_count']}")
print(f"Blocked: {ledger['blocked_count']}")
print(f"Rate: {ledger['resolution_rate']}")
