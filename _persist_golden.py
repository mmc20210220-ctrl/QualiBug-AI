"""Check attempt field names and persist Golden Set."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

p = Path("_scan_result_latest.json")
with open(p, encoding="utf-8") as f:
    result = json.load(f)

ledger = result.get("obligation_attempt_ledger") or {}
attempts = ledger.get("attempts") or []

# Check actual field names in attempts
if attempts:
    print("=== ATTEMPT FIELD NAMES ===")
    print(sorted(attempts[0].keys()))
    
    # Check reason_code field
    print("\n=== REASON_CODE DISTRIBUTION ===")
    rc = {}
    for a in attempts:
        r = a.get("reason_code") or a.get("terminal_reason") or "NONE"
        rc[r] = rc.get(r, 0) + 1
    for k, v in sorted(rc.items(), key=lambda x: -x[1])[:15]:
        print(f"  {k}: {v}")

# Now persist Golden Set from DELIVERABLE obligations
print("\n=== PERSISTING GOLDEN SET ===")
deliverable = [a for a in attempts if a.get("terminal_status") == "DELIVERABLE"]
print(f"Total DELIVERABLE: {len(deliverable)}")

# Select Golden Set: prioritize diversity
# Plan: 5 causal + 5 conservation + 5 state + 5 authorization
# Available: authorization=177, validation=77, conservation=2
# Adapt: take what we can from each type
golden_by_type = {}
for a in deliverable:
    rf = a.get("risk_family", "unknown")
    golden_by_type.setdefault(rf, []).append(a)

print("DELIVERABLE by risk_family:")
for rf, items in sorted(golden_by_type.items(), key=lambda x: -len(x[1])):
    print(f"  {rf}: {len(items)}")

# Select up to 5 from each type, prioritizing conservation and state
golden_ids = []
priority_types = ["conservation", "state", "state_integrity", "consistency", 
                  "authorization", "validation", "invariant", "isolation", "lifecycle"]
for rf in priority_types:
    items = golden_by_type.get(rf, [])
    take = min(5, len(items))
    for item in items[:take]:
        golden_ids.append(item.get("obligation_id"))
    if take > 0:
        print(f"  Selected {take} from {rf}")

# If still < 20, fill from authorization
if len(golden_ids) < 20:
    remaining = 20 - len(golden_ids)
    auth_items = golden_by_type.get("authorization", [])
    for item in auth_items[5:5+remaining]:  # skip first 5 already taken
        golden_ids.append(item.get("obligation_id"))
    print(f"  Filled {min(remaining, len(auth_items)-5)} more from authorization")

print(f"\nGolden Set size: {len(golden_ids)}")

# Save Golden Set
golden_set = {
    "schema_version": "qualibug.golden-obligation-set.v1",
    "description": "Fixed regression set selected from DELIVERABLE obligations",
    "obligation_ids": golden_ids,
    "selection_criteria": "DELIVERABLE terminal status, diverse risk_family coverage",
    "risk_family_distribution": {},
}
for oid in golden_ids:
    for a in deliverable:
        if a.get("obligation_id") == oid:
            rf = a.get("risk_family", "unknown")
            golden_set["risk_family_distribution"][rf] = golden_set["risk_family_distribution"].get(rf, 0) + 1
            break

out_path = Path("ai_test_asset_center/golden_set.json")
out_path.write_text(json.dumps(golden_set, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"Saved to {out_path}")
print(f"Distribution: {golden_set['risk_family_distribution']}")

# Verify Golden Set obligations are in plan (not DEFERRED)
golden_in_plan = 0
golden_deferred = 0
for a in attempts:
    if a.get("obligation_id") in golden_ids:
        if a.get("terminal_status") == "DEFERRED":
            golden_deferred += 1
        else:
            golden_in_plan += 1
print(f"\nGolden Set plan entry: {golden_in_plan}/{len(golden_ids)} in plan, {golden_deferred} deferred")
print(f"Plan entry rate: {golden_in_plan/len(golden_ids)*100:.1f}%")
