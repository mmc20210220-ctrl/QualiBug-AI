"""Direct V12 pipeline test to see errors."""
import traceback
import sys
sys.path.insert(0, ".")

try:
    from ai_test_asset_center.v12_pipeline import run_v12_pipeline
    from pathlib import Path

    result = run_v12_pipeline(
        root=Path("."),
        project="benchmark_mall_131",
        base_url="http://localhost:8080",
        source_manifest={
            "source_id": "src_cffd9ab1957b6659",
            "source_hash": "0be1f6e059dcfacf7089b3aeb634b4b951d56bda95e892c9e3724cc704f632fa",
        },
        environment_type="test",
        environment_ref="benchmark_local",
    )
    print(f"Result type: {type(result)}")
    if isinstance(result, dict):
        print(f"Keys: {list(result.keys())[:20]}")
        print(f"total_findings: {result.get('total_findings')}")
        print(f"execution_status: {result.get('execution_status')}")
        print(f"grade: {result.get('grade')}")
    else:
        print(f"Result: {str(result)[:500]}")
except Exception as e:
    print(f"ERROR: {type(e).__name__}: {e}")
    traceback.print_exc()
