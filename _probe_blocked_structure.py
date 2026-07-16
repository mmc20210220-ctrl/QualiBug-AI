"""Probe: (1) confirm observability fix surfaced binding_detail in failed fixture
receipts; (2) discover where per-attempt terminal block reason + context lives,
so the deep bottleneck dive knows which fields to extract."""
import json
from collections import Counter
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]
print(f"total attempts: {len(attempts)}")

# (1) fix verification: do failed fixture receipts now carry binding_detail?
fix_has_detail = 0
fix_missing = 0
sample_detail = None
for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    for rec in bundle.get("contract_evidence_receipts", []):
        if rec.get("kind") == "fixture" and rec.get("status") == "FAILED":
            ev = rec.get("evidence") or {}
            if ev.get("binding_detail"):
                fix_has_detail += 1
                if sample_detail is None:
                    sample_detail = ev
            else:
                fix_missing += 1
print(f"\n[FIX CHECK] failed fixture receipts WITH binding_detail: {fix_has_detail}, WITHOUT: {fix_missing}")
if sample_detail:
    print("  sample evidence:", json.dumps(sample_detail, ensure_ascii=False)[:400])

# (2) find terminal reason per attempt + where context lives
# scan attempt top-level keys
print("\n[STRUCT] attempt top-level keys:", list(attempts[0].keys()))

# find terminal_reason field candidates
def find_terminal(a):
    for k in ("terminal_reason", "terminal_status", "status", "reason_code", "block_reason"):
        if k in a:
            return k, a[k]
    return None, None

term_counter = Counter()
for a in attempts:
    k, v = find_terminal(a)
    term_counter[(k, v)] += 1
print("\nterminal field candidates (top):")
for (k, v), c in term_counter.most_common(12):
    print(f"  {k}={v}  x{c}")

# dump one BLOCKED_NON_REVERSIBLE_WRITE attempt fully (trimmed)
print("\n--- sample BLOCKED_NON_REVERSIBLE_WRITE attempt ---")
for a in attempts:
    blob = json.dumps(a, ensure_ascii=False)
    if "BLOCKED_NON_REVERSIBLE_WRITE" in blob:
        print("keys:", list(a.keys()))
        print(json.dumps(a, ensure_ascii=False, indent=1)[:2500])
        break

print("\n--- sample BLOCKED_MISSING_OBSERVER attempt ---")
for a in attempts:
    blob = json.dumps(a, ensure_ascii=False)
    if "BLOCKED_MISSING_OBSERVER" in blob:
        print("keys:", list(a.keys()))
        print(json.dumps(a, ensure_ascii=False, indent=1)[:2500])
        break
