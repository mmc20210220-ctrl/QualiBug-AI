"""Quick server check then run full scan."""
import sys, json, traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import urllib.request, urllib.error

# 1. Check server
try:
    r = urllib.request.urlopen("http://localhost:8080/")
    print(f"[server] Status: {r.status}")
except urllib.error.HTTPError as e:
    print(f"[server] HTTP {e.code} - server is UP")
except Exception as e:
    print(f"[server] ERROR: {e}")
    print("Server not reachable, aborting scan.")
    sys.exit(1)

# 2. Run scan
from ai_test_asset_center.__main__ import scan

_ROOT = Path(r"d:\QualiBug-AI\QualiBug-AI-main")

# Use the benchmark_mall project with API_SPEC.md
ctx = {
    "scope_id": "benchmark_mall",
    "environment_ref": "local_test",
    "environment_type": "test",
    "source_manifest": {
        "source_id": "benchmark_mall_api_spec",
        "source_hash": "4333816fe3f9dc42f5f47da1edf4344d732cdcd09ea5e1e0015c01640172fa49",
        "source_version_id": "v1",
    },
    "execution_mode": "approved_sandbox_write",
    "test_data_contract": {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "benchmark_mall",
    },
    "runtime_interface_discovery_enabled": True,
    "runtime_interface_discovery_budget": 800,
}

try:
    result = scan(
        "benchmark_mall",
        api_doc_path="projects/benchmark_mall/input/API_SPEC.md",
        base_url="http://localhost:8080",
        output_dir=Path("platform_outputs/benchmark_mall"),
        campaign_context=ctx,
    )
    Path("_scan_result_latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    findings = result.get("findings") or []
    print(f"\n[RESULT] success={result.get('success')} findings={len(findings)}")
    if not result.get("success"):
        print(f"  error: {result.get('error')}")
    
    # Print funnel summary if available
    funnel = result.get("discovery_funnel") or result.get("funnel") or {}
    if funnel:
        print(f"\n[FUNNEL] {json.dumps(funnel, indent=2, ensure_ascii=False, default=str)[:2000]}")
    
    # Print business obligation summary if available
    biz = result.get("business_obligation_summary") or {}
    if biz:
        print(f"\n[BUSINESS_SUMMARY] {json.dumps(biz, indent=2, ensure_ascii=False, default=str)[:2000]}")
        
except Exception as exc:
    print(f"SCAN_EXCEPTION: {exc}")
    traceback.print_exc()
    sys.exit(1)
