"""Test scan directly."""
import sys
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from pathlib import Path
from ai_test_asset_center.__main__ import scan

result = scan(
    project="benchmark_mall_131",
    root=Path(r"d:\QualiBug-AI\QualiBug-AI-main"),
    api_doc_text="",
    base_url="http://localhost:8080",
    campaign_context={
        "source_manifest": {
            "source_id": "src_6efcb7cd38ce74d3",
            "source_hash": "99a0209c562b736d13a2bd35a81ba6d2b2a5b2f00941a806f1d7ed3118b57703",
        },
        "environment_type": "test",
        "environment_ref": "benchmark_local",
        "scope_id": "benchmark_mall_131",
        "target_id": "benchmark_mall_131",
    },
)
print("success:", result.get("success"))
print("error:", result.get("error", "")[:200])
print("total_findings:", result.get("total_findings"))
print("total_ms:", result.get("total_ms"))
print("keys:", list(result.keys())[:20])
