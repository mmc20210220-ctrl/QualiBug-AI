#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""List all behavior_ir operations and locate bir_* IDs."""
import json
from pathlib import Path

scan = json.loads(Path("platform_outputs/benchmark_mall_131/scan_result.json").read_text(encoding="utf-8"))
blob = json.dumps(scan, ensure_ascii=False, default=str)
print("bir_588a7915b5421e5e occurrences:", blob.count("bir_588a7915b5421e5e"))

bir = scan["v12"]["behavior_ir"]
lines = []
for o in bir.get("operations") or []:
    lines.append({
        "method": o.get("method"),
        "path": o.get("path"),
        "operation_id": o.get("operation_id"),
        "read_write": o.get("read_write"),
        "side_effect_class": o.get("side_effect_class"),
        "entity_refs": o.get("entity_refs"),
    })
Path("_bir_all_ops.json").write_text(json.dumps(lines, indent=2, ensure_ascii=False), encoding="utf-8")
for l in lines:
    print(f"  {str(l['method']):6s} {str(l['path']):42s} rw={l['read_write']} side={l['side_effect_class']}")

# Where do bir_* live? check behavior_ir_expansion
bie = scan["v12"].get("behavior_ir_expansion") or {}
print("behavior_ir_expansion keys:", sorted(bie.keys()) if isinstance(bie, dict) else type(bie))
