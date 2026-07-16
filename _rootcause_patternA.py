"""Drill into a Pattern A attempt (control+treatment also FAILED) and confirm the
shared-fixture lifecycle hypothesis across all 129 attempts."""
import json
from collections import Counter
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

# Find first Pattern A attempt (has CONTROL_RECEIPT_FAILED:control_1)
target = None
for i, a in enumerate(attempts):
    blob = json.dumps(a.get("delivery_evidence_bundle", {}), ensure_ascii=False)
    if "CONTROL_RECEIPT_FAILED:control_1" in blob and "FIXTURE_RECEIPT_FAILED:fix_37e4859011c9c713" in blob:
        target = i
        break

print(f"Pattern A sample attempt index = {target}")
a = attempts[target]
bundle = a["delivery_evidence_bundle"]
oracle = bundle["oracle_receipt"]
print("oracle.status =", oracle.get("status"))
print("activation.reason_codes =", oracle["activation_receipt"].get("reason_codes"))
print("\n-- control_1 receipt (FULL evidence) --")
for rec in bundle["contract_evidence_receipts"]:
    if rec.get("subject_id") == "control_1":
        print(json.dumps(rec, ensure_ascii=False, indent=2)[:1500])
print("\n-- treatment_1 receipt (FULL evidence) --")
for rec in bundle["contract_evidence_receipts"]:
    if rec.get("subject_id") == "treatment_1":
        print(json.dumps(rec, ensure_ascii=False, indent=2)[:1500])
print("\n-- http_response observer receipt (FULL evidence) --")
for rec in bundle.get("observer_receipts", []):
    if rec.get("observer_id") == "http_response":
        print(json.dumps(rec, ensure_ascii=False, indent=2)[:1500])

# Shared-fixture hypothesis: across ALL attempts, status + value_fingerprint of fix_37e4859011c9c713
print("\n-- fix_37e4859011c9c713 across ALL attempts --")
fp_counter = Counter()
status_all = Counter()
for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    for rec in bundle.get("contract_evidence_receipts", []):
        if rec.get("subject_id") == "fix_37e4859011c9c9c9c713" or rec.get("subject_id") == "fix_37e4859011c9c713":
            status_all[rec.get("status")] += 1
            fp_counter[rec.get("evidence", {}).get("value_fingerprint", "<none>")] += 1
print("status distribution:", dict(status_all))
print("value_fingerprint distribution (top):", fp_counter.most_common(3))

# Among the 14 OBSERVED, are their obligations delivered as bugs?
delivered_with_fix = 0
for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    fix_ok = any(r.get("subject_id")=="fix_37e4859011c9c713" and r.get("status")=="OBSERVED"
                 for r in bundle.get("contract_evidence_receipts", []))
    if fix_ok:
        blob = json.dumps(bundle, ensure_ascii=False)
        if '"VIOLATION"' in blob and "DELIVERABLE" in blob:
            delivered_with_fix += 1
print("obligations where fix_37e4859011c9c713 OBSERVED AND delivered as bug:", delivered_with_fix)
