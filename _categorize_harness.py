"""Categorize the 15 harness-failed attempts: distinct reason-code patterns,
confirm the shared fixture id, and dump the full failed-fixture receipt."""
import json
from collections import Counter, defaultdict
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

idxs = [i for i, a in enumerate(attempts)
        if "CONTRACT_ORACLE_HARNESS_FAILED" in json.dumps(a, ensure_ascii=False)]

# 1) distinct reason_code sets among the 15
patterns = Counter()
fixture_ids_failed = Counter()
for i in idxs:
    bundle = attempts[i].get("delivery_evidence_bundle") or {}
    oracle = bundle.get("oracle_receipt") or {}
    act = oracle.get("activation_receipt") or {}
    rc = tuple(sorted(act.get("reason_codes") or []))
    patterns[rc] += 1
    for rec in bundle.get("contract_evidence_receipts") or []:
        if rec.get("kind") == "fixture" and rec.get("status") == "FAILED":
            fixture_ids_failed[rec.get("subject_id")] += 1

print("DISTINCT activation.reason_codes patterns among 15:")
for rc, c in patterns.most_common():
    print(f"  x{c}: {list(rc)}")
print("\nFAILED fixture subject_ids across the 15:")
for fid, c in fixture_ids_failed.most_common():
    print(f"  x{c}: {fid}")

# 2) How many attempts reference fix_37e4859011c9c713 and its status distribution (all 129)
fix_status = Counter()
for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    for rec in bundle.get("contract_evidence_receipts") or []:
        if rec.get("subject_id") == "fix_37e4859011c9c713":
            fix_status[rec.get("status")] += 1
print("\nfix_37e4859011c9c713 status across ALL 129 attempts:", dict(fix_status))

# 3) Dump FULL raw receipt of the failed shared fixture (all keys, no truncation)
print("\nFULL failed-fixture receipt (first occurrence):")
for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    for rec in bundle.get("contract_evidence_receipts") or []:
        if rec.get("subject_id") == "fix_37e4859011c9c713" and rec.get("status") == "FAILED":
            print(json.dumps(rec, ensure_ascii=False, indent=2)[:2000])
            raise SystemExit
