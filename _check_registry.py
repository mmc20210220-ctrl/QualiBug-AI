"""Check source registry."""
import os
import json

path = r"d:\QualiBug-AI\QualiBug-AI-main\platform_workspace\benchmark_mall_131\source_registry\registry.json"
print(f"registry exists: {os.path.exists(path)}")
if os.path.exists(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    assets = data.get("assets", {})
    print(f"assets count: {len(assets)}")
    for k, v in list(assets.items())[:3]:
        sid = v.get("source_id", "")[:30]
        sh = v.get("latest_source_hash", "")[:20]
        print(f"  {k}: source_id={sid}, hash={sh}...")
