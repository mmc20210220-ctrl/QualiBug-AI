"""Test source loading."""
import sys
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from pathlib import Path
from ai_test_asset_center.scan_source_runtime import _load_registered_source

root = Path(r"d:\QualiBug-AI\QualiBug-AI-main")
project = "benchmark_mall_131"
context = {
    "source_manifest": {
        "source_id": "src_6efcb7cd38ce74d3",
        "source_hash": "99a0209c562b736d13a2bd35a81ba6d2b2a5b2f00941a806f1d7ed3118b57703",
    }
}

result = _load_registered_source(project, root, context)
print(f"result length: {len(result)}")
print(f"result preview: {result[:200] if result else 'EMPTY'}")
