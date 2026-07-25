#!/usr/bin/env python3
"""Project F MES Blind Test Runner"""
import os
import sys
import json
import time
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

# Set environment for blind test
os.environ.setdefault("QUALIBUG_JWT_SECRET", "dev-mode-only")
os.environ["QUALIBUG_TARGET_BASE_URL"] = "http://localhost:8020"
os.environ["QUALIBUG_SSRF_ALLOW_INTERNAL"] = "1"
os.environ["QUALIBUG_UNIFY_ANALYZERS"] = "1"
os.environ["QUALIBUG_SCAN_MAX_ROUNDS"] = "3"
os.environ["ENABLE_V12_STATE_GRAPH_ENGINE"] = "true"

PROJECT = "mes_f"
BASE_URL = "http://localhost:8020"

def main():
    from ai_test_asset_center.__main__ import scan
    
    # Read API spec
    api_spec_path = ROOT / "platform_inputs" / PROJECT / "API_SPEC.md"
    api_doc = api_spec_path.read_text(encoding="utf-8")
    source_hash = hashlib.sha256(api_doc.encode("utf-8")).hexdigest()
    
    # Read PRD
    prd_path = ROOT / "platform_inputs" / PROJECT / "PRD.md"
    prd_text = prd_path.read_text(encoding="utf-8") if prd_path.exists() else ""
    
    print(f"Starting blind test scan for project: {PROJECT}")
    print(f"Target: {BASE_URL}")
    print(f"API Spec hash: {source_hash[:16]}...")
    print(f"PRD length: {len(prd_text)} chars")
    print("-" * 60)
    
    started = time.time()
    
    result = scan(
        project=PROJECT,
        root=ROOT,
        api_doc_text=api_doc,
        prd_text=prd_text,
        base_url=BASE_URL,
        ci_gate=False,
        multi_layer=True,
        save_report=True,
        campaign_context={
            "scope_id": "project_f_blind_test",
            "environment_ref": "mes_f_test",
            "environment_kind": "test",
            "environment_type": "test",
            "runtime": {"environment_type": "test", "environment_kind": "test"},
            "source_manifest": {
                "source_id": f"{PROJECT}/API_SPEC.md",
                "source_hash": source_hash
            },
        },
    )
    
    elapsed = time.time() - started
    
    print("-" * 60)
    print(f"Scan completed in {elapsed:.1f}s")
    print(f"Success: {result.get('success')}")
    print(f"Execution status: {result.get('execution_status')}")
    print(f"Total findings: {result.get('total_findings')}")
    print(f"Total candidates: {result.get('total_candidates')}")
    
    # Save result
    output_path = ROOT / "projects" / PROJECT / "blind_test_result.json"
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Result saved to: {output_path}")
    
    return 0 if result.get("success") else 1

if __name__ == "__main__":
    sys.exit(main())
