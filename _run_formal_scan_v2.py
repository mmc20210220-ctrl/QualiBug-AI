#!/usr/bin/env python
"""P0-13: Run formal scan v2 - with correct source and fresh output."""
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding='utf-8')

from ai_test_asset_center.__main__ import scan


def main():
    root = Path(".")
    project = "benchmark_mall"

    # Step 1: Compute correct hash from actual file
    api_spec_path = Path("projects/benchmark_mall/input/API_SPEC.md")
    api_doc_text = api_spec_path.read_text(encoding="utf-8")
    actual_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()
    print(f"[1] API_SPEC.md hash: {actual_hash[:24]}...")

    # Step 2: Use the active source from registry with verified hash
    source_manifest = {
        "source_id": "src_fe4c9062370d3c58",
        "source_hash": actual_hash,
    }
    print(f"[2] Source manifest: {source_manifest['source_id']}")

    # Step 3: Run scan
    print(f"[3] Running formal scan...")
    started = time.time()

    campaign_context = {
        "source_manifest": source_manifest,
        "scope_id": project,
        "environment_type": "test",
        "environment_ref": "test",
        "execution_mode": "approved_sandbox_write",
    }

    result = scan(
        project=project,
        root=root,
        base_url="http://localhost:8080",
        api_doc_text=api_doc_text,  # Pass text directly to avoid path issues
        save_report=False,
        campaign_context=campaign_context,
    )

    elapsed = time.time() - started
    print(f"\n{'='*60}")
    print(f"SCAN RESULTS (elapsed: {elapsed:.1f}s)")
    print(f"{'='*60}")
    print(f"Success: {result.get('success')}")
    print(f"Grade: {result.get('grade')}")
    print(f"Total findings: {result.get('total_findings')}")
    print(f"Total candidates: {result.get('total_candidates')}")
    print(f"Execution status: {result.get('execution_status')}")

    # Check input_gaps
    gaps = result.get("input_gaps", [])
    if gaps:
        print(f"\nInput gaps:")
        for g in gaps:
            print(f"  {g.get('code')}: {g.get('detail', '')[:80]}")

    # Check campaign
    campaign = result.get("campaign", {})
    print(f"\nCampaign: {campaign.get('campaign_id', 'none')}")
    print(f"  source_id: {campaign.get('source_id')}")
    print(f"  source_hash: {str(campaign.get('source_hash',''))[:24]}...")
    print(f"  status: {campaign.get('campaign_status')}")

    # Check runtime_contract source_manifest
    rc = result.get("runtime_contract", {})
    rc_manifest = rc.get("source_manifest", {})
    print(f"\nRuntime contract source_manifest:")
    print(f"  source_id: {rc_manifest.get('source_id')}")
    print(f"  source_hash: {str(rc_manifest.get('source_hash',''))[:24]}...")

    # Check findings
    findings = result.get("findings", [])
    candidates = result.get("candidate_findings", [])
    all_f = findings + candidates
    print(f"\nFindings: {len(findings)}, Candidates: {len(candidates)}")

    # Non-auth findings
    non_auth = [f for f in all_f if isinstance(f, dict)
                and f.get("category", f.get("risk_family", "")) not in
                ("authorization", "validation", "permission_boundary")]
    print(f"Non-auth/validation: {len(non_auth)}")
    for f in non_auth[:5]:
        print(f"  [{f.get('category', f.get('risk_family'))}] {f.get('title', '?')[:60]}")

    # Save
    out_path = Path("_scan_result_p13_v2.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
