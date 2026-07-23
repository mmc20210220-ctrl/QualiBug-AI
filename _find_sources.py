#!/usr/bin/env python
"""Find active sources in the registry."""
import json
from pathlib import Path

data = json.loads(Path("platform_workspace/benchmark_mall/enterprise_knowledge_center/source_registry.json").read_text(encoding="utf-8"))
sources = data.get("sources", [])
active = [s for s in sources if s.get("status") == "active"]
print(f"Total sources: {len(sources)}")
print(f"Active sources: {len(active)}")
for s in active[:5]:
    print(f"  {s.get('source_id')}: {s.get('original_name')}")
    print(f"    hash: {s.get('content_hash')}")
