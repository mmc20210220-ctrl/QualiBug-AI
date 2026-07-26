"""Inspect source registry and run scan with proper parameters."""
import json
import sys
import os

# Check source registry
reg_path = "platform_workspace/benchmark_mall/enterprise_knowledge_center/source_registry.json"
reg = json.load(open(reg_path, encoding="utf-8"))
print("Registry keys:", sorted(reg.keys())[:15])
sources = reg.get("sources", [])
print(f"Sources count: {len(sources)}")
for s in sources[:8]:
    if isinstance(s, dict):
        st = s.get("source_type", "?")
        title = s.get("title", s.get("name", "?"))
        print(f"  - {st} / {str(title)[:60]}")

# Check if API doc is registered
api_doc = ""
prd_text = ""
for s in sources:
    if not isinstance(s, dict):
        continue
    st = str(s.get("source_type", "")).lower()
    content = s.get("content", s.get("text", ""))
    if "api" in st or "openapi" in st or "swagger" in st:
        api_doc = content or s.get("path", "")
        print(f"\nAPI doc found: type={st}, len={len(str(content))}")
    if "prd" in st or "requirement" in st or "product" in st:
        prd_text = content or s.get("path", "")
        print(f"PRD found: type={st}, len={len(str(content))}")

# Also check if there's a behavior_ir already
ir_candidates = [
    "platform_workspace/benchmark_mall/defect_discovery/behavior_ir.json",
    "platform_workspace/benchmark_mall/behavior_ir.json",
]
for p in ir_candidates:
    if os.path.exists(p):
        ir = json.load(open(p, encoding="utf-8"))
        ops = ir.get("operations", [])
        print(f"\nBehavior IR found: {p}")
        print(f"  Operations: {len(ops)}")
        break
else:
    # Search for it
    dd_dir = "platform_workspace/benchmark_mall/defect_discovery"
    if os.path.isdir(dd_dir):
        files = os.listdir(dd_dir)
        print(f"\ndefect_discovery contents: {files[:20]}")
