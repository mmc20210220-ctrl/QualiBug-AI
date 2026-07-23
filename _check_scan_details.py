"""Check scan_result.json details."""
from pathlib import Path
import json

sr_file = Path("platform_outputs/real_project_demo/scan_result.json")
if sr_file.exists():
    s = json.load(open(sr_file, "r", encoding="utf-8"))
    print("scan_result.json keys:", list(s.keys())[:20])
    print(f"\nsuccess: {s.get('success')}")
    print(f"total_findings: {s.get('total_findings')}")
    print(f"total_candidates: {s.get('total_candidates')}")
    print(f"execution_status: {s.get('execution_status')}")
    
    # Check campaign
    campaign = s.get("campaign", {})
    if isinstance(campaign, dict):
        print(f"\ncampaign_status: {campaign.get('campaign_status')}")
        print(f"campaign_id: {campaign.get('campaign_id', '')[:40]}")
    
    # Check coverage gaps
    gaps = s.get("coverage_gaps", [])
    print(f"\ncoverage_gaps: {len(gaps)}")
    for g in gaps[:5]:
        kind = g.get("kind", "")
        code = g.get("code", "")
        detail = g.get("detail", "")[:80]
        print(f"  {kind}: {code} - {detail}")
    
    # Check runtime contract
    rc = s.get("runtime_contract", {})
    if isinstance(rc, dict):
        print(f"\nruntime_contract status: {rc.get('status')}")
        print(f"runtime_contract reason: {rc.get('reason')}")
    
    # Check layers
    layers = s.get("layers", {})
    if isinstance(layers, dict):
        print(f"\nlayers keys: {list(layers.keys())[:10]}")
        sgd = layers.get("source_grounded_discovery", {})
        if isinstance(sgd, dict):
            print(f"  source_grounded_discovery execution_status: {sgd.get('execution_status')}")
            print(f"  source_grounded_discovery ms: {sgd.get('ms')}")
