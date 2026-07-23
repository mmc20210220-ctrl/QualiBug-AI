#!/usr/bin/env python
"""Project B Blind Test: Run full scan on Equipment Maintenance system."""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

# Disable LLM reasoning to avoid 300s timeout on unreachable API.
# load_dotenv() does NOT override existing env vars, so setting them empty
# here prevents .env.local from re-enabling LLM.
os.environ["LLM_BASE_URL"] = ""
os.environ["LLM_API_KEY"] = ""
os.environ["LLM_MODEL"] = ""
os.environ.pop("DEEPSEEK_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

sys.path.insert(0, str(Path(__file__).parent))
sys.stdout.reconfigure(encoding='utf-8')

from ai_test_asset_center.__main__ import scan


def main():
    root = Path(".")
    project = "equipment_maintenance"

    # Step 1: Read API spec
    api_spec_path = Path("projects/equipment_maintenance/input/API_SPEC.md")
    api_doc_text = api_spec_path.read_text(encoding="utf-8")
    actual_hash = hashlib.sha256(api_doc_text.encode("utf-8")).hexdigest()
    print(f"[1] API_SPEC.md hash: {actual_hash[:24]}...")

    # Step 2: Source manifest
    source_manifest = {
        "source_id": f"src_{actual_hash[:16]}",
        "source_hash": actual_hash,
    }
    print(f"[2] Source manifest: {source_manifest['source_id']}")

    # Step 3: Run scan against mock server on port 9090
    print(f"[3] Running Project B scan against http://localhost:9090 ...")
    started = time.time()

    campaign_context = {
        "source_manifest": source_manifest,
        "scope_id": project,
        "environment_type": "test",
        "environment_ref": "equipment-maintenance-mock",
        "execution_mode": "approved_sandbox_write",
    }

    result = scan(
        project=project,
        root=root,
        base_url="http://localhost:9090",
        api_doc_text=api_doc_text,
        save_report=False,
        campaign_context=campaign_context,
    )

    elapsed = time.time() - started
    print(f"\n{'='*60}")
    print(f"PROJECT B SCAN RESULTS (elapsed: {elapsed:.1f}s)")
    print(f"{'='*60}")
    print(f"Success: {result.get('success')}")
    print(f"Grade: {result.get('grade')}")
    print(f"Total findings: {result.get('total_findings')}")
    print(f"Total candidates: {result.get('total_candidates')}")
    print(f"Execution status: {result.get('execution_status')}")

    # Input gaps
    gaps = result.get("input_gaps", [])
    if gaps:
        print(f"\nInput gaps:")
        for g in gaps:
            print(f"  {g.get('code')}: {g.get('detail', '')[:80]}")

    # Campaign
    campaign = result.get("campaign", {})
    print(f"\nCampaign: {campaign.get('campaign_id', 'none')}")
    print(f"  status: {campaign.get('campaign_status')}")

    # Findings breakdown
    findings = result.get("findings", [])
    candidates = result.get("candidate_findings", [])
    all_f = findings + candidates
    print(f"\nFindings: {len(findings)}, Candidates: {len(candidates)}")

    # Category breakdown
    cats = {}
    for f in all_f:
        if isinstance(f, dict):
            cat = f.get("category", f.get("risk_family", "unknown"))
            cats[cat] = cats.get(cat, 0) + 1
    print(f"\nCategory breakdown:")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    # Non-auth findings (deep business)
    non_auth = [f for f in all_f if isinstance(f, dict)
                and f.get("category", f.get("risk_family", "")) not in
                ("authorization", "validation", "permission_boundary")]
    print(f"\nDeep business findings: {len(non_auth)}")
    for f in non_auth[:10]:
        print(f"  [{f.get('category', f.get('risk_family'))}] {f.get('title', '?')[:70]}")

    # Save result
    out_path = Path("_scan_result_project_b.json")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nSaved: {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
