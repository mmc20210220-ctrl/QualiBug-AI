"""Direct scan call to see actual error."""
from pathlib import Path
import sys
import json
import time
sys.path.insert(0, str(Path(".").resolve()))

project = "contractflow_project_c"
root = Path(".")

# Load API doc from source registry
api_spec_path = root / "platform_workspace" / project / "enterprise_knowledge_center" / "sources" / "src_68e5e273aaf8f71e_v1_API_SPEC.md"
api_doc_text = api_spec_path.read_text(encoding="utf-8")
print(f"API doc: {len(api_doc_text)} chars")

# Load PRD
prd_path = root / "platform_workspace" / project / "enterprise_knowledge_center" / "sources" / "src_f6008c42314fa17d_v1_PRD.md"
prd_text = prd_path.read_text(encoding="utf-8") if prd_path.exists() else ""
print(f"PRD: {len(prd_text)} chars")

campaign_context = {
    "scope_id": "contractflow-local",
    "environment_ref": "sandbox",
    "environment_type": "test",
    "mainline_authority": "experiment_candidate",
    "execution_mode": "approved_sandbox_write",
    "source_manifest": {
        "source_id": "src_68e5e273aaf8f71e",
        "source_hash": "c87f5306e31b65b34e90fa8fbc79b555afb15e68ba7b665cdfb88335e3e1efa5",
        "source_version_id": "srcv_c87f5306e31b65b34e90fa8f",
        "source_origin": "registered_source_registry",
    },
    "test_data_contract": {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "contractflow-local",
    },
}

print(f"\nCalling scan()...")
print(f"  project: {project}")
print(f"  base_url: http://localhost:8000")
print(f"  campaign: (auto-generated)")
start = time.time()

try:
    from ai_test_asset_center.__main__ import scan
    result = scan(
        project=project,
        root=root,
        prd_text=prd_text,
        api_doc_text=api_doc_text,
        base_url="http://localhost:8000",
        multi_layer=True,
        campaign_context=campaign_context,
    )
    elapsed = time.time() - start
    print(f"\nScan completed in {elapsed:.1f}s")
    print(f"  success: {result.get('success')}")
    print(f"  error: {result.get('error', 'none')}")
    print(f"  total_findings: {result.get('total_findings')}")
    print(f"  execution_status: {result.get('execution_status')}")
    
    # Check bootstrap
    bootstrap = result.get("test_data_bootstrap", {})
    print(f"\n  test_data_bootstrap:")
    print(f"    status: {bootstrap.get('status')}")
    print(f"    reason: {bootstrap.get('reason')}")
    if bootstrap.get("creation_receipt"):
        print(f"    creation_receipt_id: {bootstrap['creation_receipt'].get('receipt_id')}")
    
    # Check test_data_plan
    plan = result.get("test_data_plan", {})
    print(f"\n  test_data_plan:")
    print(f"    status: {plan.get('status')}")
    print(f"    missing: {plan.get('missing_requirements')}")
    
    # Save full result
    with open("_scan_result_final.json", "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  Full result saved to _scan_result_final.json")
    
except Exception as exc:
    elapsed = time.time() - start
    import traceback
    print(f"\nScan FAILED after {elapsed:.1f}s")
    print(f"  {type(exc).__name__}: {exc}")
    traceback.print_exc()
