import json
from collections import Counter

p = r"D:/QualiBug-AI/QualiBug-AI-main/_funnel_runs/optimized.json"
d = json.load(open(p, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

# Find cleanup failure details in cart POST attempts
for a in attempts:
    if a.get("reason_code") != "CONTRACT_ORACLE_HARNESS_FAILED":
        continue
    if "bir_dff5e016338935e6" not in (a.get("operation_refs") or []):
        continue
    if a.get("risk_family") != "authorization":
        continue
    print("obl", a.get("obligation_id"))
    # stages list cleanup detail
    for st in a.get("stages") or []:
        if isinstance(st, dict) and st.get("stage") == "cleanup":
            print("cleanup stage", json.dumps(st, ensure_ascii=False)[:2000])
    op = a.get("operational_receipt") or {}
    print("operational", json.dumps(op, ensure_ascii=False)[:2500])
    deb = a.get("delivery_evidence_bundle") or {}
    for k in ("execution_receipt", "observer_receipts", "contract_evidence_receipts"):
        val = deb.get(k)
        if not val:
            continue
        s = json.dumps(val, ensure_ascii=False)
        if "cleanup" in s.lower() or "DELETE" in s or "cart" in s:
            # print cleanup-related slices
            idx = s.lower().find("cleanup")
            while idx >= 0 and idx < len(s):
                print(k, "ctx", s[max(0, idx - 80) : idx + 220])
                idx = s.lower().find("cleanup", idx + 7)
                if idx > 5000:
                    break
    # also search whole attempt for cleanup status codes
    blob = json.dumps(a, ensure_ascii=False)
    for token in [
        "cleanup_failures",
        "restoration_verified",
        "state_unchanged",
        "cleanup_write",
        "status_code",
        "DELETE /api/cart",
        "binding",
        "item_id",
        "cartItem",
    ]:
        i = blob.find(token)
        if i >= 0:
            print("tok", token, blob[max(0, i - 60) : i + 180])
    break

# Aggregate CLEANUP_RECEIPT_FAILED targets
targets = Counter()
for a in attempts:
    if a.get("reason_code") != "CONTRACT_ORACLE_HARNESS_FAILED":
        continue
    blob = json.dumps(a, ensure_ascii=False)
    import re

    for m in re.findall(r"CLEANUP_RECEIPT_FAILED:[^\"\\]+", blob):
        targets[m] += 1
print("\nCLEANUP targets:", targets.most_common())
