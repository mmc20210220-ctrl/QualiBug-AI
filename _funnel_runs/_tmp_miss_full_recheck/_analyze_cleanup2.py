import json

p = r"D:/QualiBug-AI/QualiBug-AI-main/_funnel_runs/optimized.json"
d = json.load(open(p, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

for a in attempts:
    if a.get("reason_code") != "CONTRACT_ORACLE_HARNESS_FAILED":
        continue
    if "bir_dff5e016338935e6" not in (a.get("operation_refs") or []):
        continue
    if a.get("risk_family") != "authorization":
        continue
    blob = json.dumps(a, ensure_ascii=False)
    for token in [
        "cleanup_reason",
        "cleanup_binding",
        "cleanup_status",
        "cleanup_failures",
        "accepted_governed_writes_requiring",
        "cleanup_compensation",
        "cleanup_original",
        "cleanup_body",
        "missing_bindings",
        ":id",
        "cart/items",
    ]:
        start = 0
        hits = 0
        while hits < 3:
            i = blob.find(token, start)
            if i < 0:
                break
            print(token, "=>", blob[max(0, i - 40) : i + 160].replace("\n", " "))
            start = i + len(token)
            hits += 1
    # observations in delivery bundle finding/raw
    deb = a.get("delivery_evidence_bundle") or {}
    # search steps with phase cleanup
    er = deb.get("execution_receipt") or {}
    # try nested
    s = json.dumps(er, ensure_ascii=False)
    if "cleanup_reason" in s:
        i = s.find("cleanup_reason")
        print("er cleanup_reason", s[i : i + 200])
    # contract evidence cleanup status
    for row in deb.get("contract_evidence_receipts") or []:
        if isinstance(row, dict) and row.get("kind") == "cleanup":
            print("cleanup evidence", json.dumps(row, ensure_ascii=False)[:1200])
    break
