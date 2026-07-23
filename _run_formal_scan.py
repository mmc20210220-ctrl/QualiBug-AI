#!/usr/bin/env python
"""P0-13: Run formal scan with correct source binding."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from ai_test_asset_center.__main__ import scan


def main():
    root = Path(".")
    project = "benchmark_mall"

    # Step 1: Get correct source manifest directly from registry
    print("[1] Loading source registry...")
    reg_path = Path("platform_workspace/benchmark_mall/enterprise_knowledge_center/source_registry.json")
    reg_data = json.loads(reg_path.read_text(encoding="utf-8"))
    sources = reg_data.get("sources", [])
    print(f"    Total sources: {len(sources)}")
    
    source_manifest = None
    for s in sources:
        sid = s.get("source_id", "")
        name = s.get("original_name", "?")
        status = s.get("status", "?")
        sh = s.get("content_hash", "")
        print(f"    {sid}: {name} [{status}] hash={sh[:16]}...")
        # Use active API_SPEC.md source
        if name == "API_SPEC.md" and status == "active" and len(sh) == 64:
            source_manifest = {"source_id": sid, "source_hash": sh}
    
    if not source_manifest:
        print("    ERROR: No valid source manifest found!")
        return 1
    
    print(f"\n    Using: {source_manifest['source_id']} hash={source_manifest['source_hash'][:16]}...")

    # Step 2: Run scan with correct manifest
    print("\n[2] Running formal scan...")
    
    campaign_context = {
        "source_manifest": source_manifest,
        "scope_id": project,
        "environment_type": "test",
        "environment_ref": "test",
    }
    
    result = scan(
        project=project,
        root=root,
        base_url="http://localhost:8080",
        api_doc_path="projects/benchmark_mall/input/API_SPEC.md",
        save_report=True,
        campaign_context=campaign_context,
    )

    # Step 3: Analyze results
    print(f"\n{'='*60}")
    print("SCAN RESULTS")
    print(f"{'='*60}")
    print(f"Success: {result.get('success')}")
    print(f"Grade: {result.get('grade')}")
    print(f"Total findings: {result.get('total_findings')}")
    print(f"Total candidates: {result.get('total_candidates')}")
    print(f"Execution status: {result.get('execution_status')}")
    
    campaign = result.get("campaign", {})
    print(f"Campaign ID: {campaign.get('campaign_id')}")
    
    # Check for conservation/state findings
    findings = result.get("findings", result.get("confirmed", []))
    candidates = result.get("candidates", [])
    all_findings = findings + candidates
    
    print(f"\nFindings: {len(findings)}, Candidates: {len(candidates)}")
    
    # Categorize
    cats = {}
    for f in all_findings:
        if not isinstance(f, dict):
            continue
        cat = f.get("category", f.get("risk_family", "unknown"))
        cats[cat] = cats.get(cat, 0) + 1
    print(f"\nBy category:")
    for cat, count in sorted(cats.items()):
        print(f"  {cat}: {count}")
    
    # Check for non-auth non-validation findings
    non_auth = [f for f in all_findings if isinstance(f, dict) 
                and f.get("category", f.get("risk_family", "")) not in ("authorization", "validation", "permission_boundary")]
    print(f"\nNon-authorization/validation findings: {len(non_auth)}")
    for f in non_auth[:5]:
        print(f"  [{f.get('category', f.get('risk_family'))}] {f.get('title', '?')[:60]}")
    
    # Save result
    out_path = Path("_scan_result_p13.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nFull result saved: {out_path}")
    
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
