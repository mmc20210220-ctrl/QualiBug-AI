"""Check scan result details."""
import json

d = json.load(open("_ecommerce_scan_result.json", "r", encoding="utf-8"))
print("execution_status:", d.get("execution_status"))
print("total_findings:", d.get("total_findings"))
print("total_ms:", d.get("total_ms"))

campaign = d.get("campaign", {})
if isinstance(campaign, dict):
    print("campaign_status:", campaign.get("campaign_status"))
    print("coverage_deferred_reason:", campaign.get("coverage_deferred_reason"))
    print("next_campaign_reason:", campaign.get("next_campaign_reason"))

# Check coverage gaps
gaps = d.get("coverage_gaps", [])
print(f"\ncoverage_gaps: {len(gaps)}")
for g in gaps[:5]:
    kind = g.get("kind", "")
    code = g.get("code", "")
    detail = g.get("detail", "")[:100]
    print(f"  {kind}: {code} - {detail}")

# Check runtime contract
rc = d.get("runtime_contract", {})
if isinstance(rc, dict):
    print(f"\nruntime_contract status: {rc.get('status')}")
    print(f"runtime_contract reason: {rc.get('reason')}")
    tp = rc.get("target_policy_decision", {})
    if isinstance(tp, dict):
        print(f"target_policy status: {tp.get('status')}")
        print(f"target_policy reason: {tp.get('reason')}")
