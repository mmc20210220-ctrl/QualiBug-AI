"""Test source content loading."""
import sys
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from pathlib import Path
from ai_test_asset_center.private_pilot_scan_context_contract import load_source_content_from_manifest

root = Path(r"d:\QualiBug-AI\QualiBug-AI-main")
project = "benchmark_mall_131"
manifest = {
    "source_id": "src_6efcb7cd38ce74d3",
    "source_hash": "99a0209c562b736d13a2bd35a81ba6d2b2a5b2f00941a806f1d7ed3118b57703",
}

content = load_source_content_from_manifest(project, root, manifest)
print(f"content length: {len(content)}")
print(f"content preview: {content[:200] if content else 'EMPTY'}")
