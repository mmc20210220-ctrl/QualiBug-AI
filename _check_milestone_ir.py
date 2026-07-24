"""Check milestone operations in Behavior IR."""
import sys, tempfile, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_test_asset_center.enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_files,
)
from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset

input_dir = Path("projects/contractflow_c/input")
files = sorted(p for p in input_dir.iterdir() if p.is_file())
with tempfile.TemporaryDirectory() as tmp:
    root_tmp = Path(tmp)
    ingest_enterprise_knowledge_files("contractflow_c", files, root=root_tmp, actor={"name": "diag", "role": "project_owner"})
    asset = build_enterprise_business_knowledge_asset("contractflow_c", root=root_tmp)
    ir = build_behavior_ir_from_knowledge_asset(asset, project_id="contractflow_c")

ops = ir.get("operations", [])
milestone_ops = [op for op in ops if "milestone" in str(op.get("path", "")).lower()]
print(f"Total ops: {len(ops)}")
print(f"Milestone ops: {len(milestone_ops)}")
for op in milestone_ops:
    method = op.get("method", "")
    path = op.get("path", "")
    op_id = op.get("id", "")[:40]
    print(f"  {method} {path} id={op_id}")

# Also check what the resolver would find
from ai_test_asset_center.runtime_binding_resolver import _find_list_endpoints_for_entity
candidates = _find_list_endpoints_for_entity(ir, "milestoneId")
print(f"\nResolver candidates for milestoneId: {len(candidates)}")
for c in candidates:
    print(f"  {c.get('method')} {c.get('path')} id={c.get('id','')[:40]}")
