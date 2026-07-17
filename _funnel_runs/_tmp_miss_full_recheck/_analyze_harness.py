import json
import re
from collections import Counter

p = r"D:/QualiBug-AI/QualiBug-AI-main/_funnel_runs/optimized.json"
d = json.load(open(p, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

dem = Counter()
for a in attempts:
    if a.get("reason_code") != "CONTRACT_ORACLE_HARNESS_FAILED":
        continue
    blob = json.dumps(a, ensure_ascii=False)
    for m in re.findall(r'"demotion_reason": "[^"]{0,120}"', blob):
        dem[m] += 1
    for m in re.findall(r'"oracle_demotion_reason": "[^"]{0,120}"', blob):
        dem[m] += 1
    for m in re.findall(r'"harness_error": [^,}\]]+', blob):
        dem[m] += 1
    for m in re.findall(r"CLEANUP_[A-Z_]+", blob):
        dem[m] += 1
    for m in re.findall(r'"activation_status": "[^"]+"', blob):
        dem[m] += 1
    for m in re.findall(r'"oracle_status": "[^"]+"', blob):
        dem[m] += 1

print("demotion/harness tokens:")
for k, v in dem.most_common(40):
    print(v, k)

targets = [
    ("bir_dff5e016338935e6", "authorization"),
    ("bir_dff5e016338935e6", "validation"),
    ("bir_210216dd365b8740", "authorization"),
    ("bir_b7b588dff7feba44", "authorization"),
]
for want_op, want_risk in targets:
    for a in attempts:
        if a.get("reason_code") != "CONTRACT_ORACLE_HARNESS_FAILED":
            continue
        if want_op not in (a.get("operation_refs") or []):
            continue
        if a.get("risk_family") != want_risk:
            continue
        print("\n####", want_op, want_risk, a.get("obligation_id"))
        adj = (a.get("gate_receipt") or {}).get("adjudication")
        print("adjudication:", json.dumps(adj, ensure_ascii=False)[:1500])
        stages = a.get("stages")
        if isinstance(stages, list):
            for st in stages:
                if not isinstance(st, dict):
                    continue
                if st.get("stage") in {
                    "oracle",
                    "gate",
                    "cleanup",
                    "observation",
                    "assertion",
                    "governed_execution",
                }:
                    print(
                        "stage",
                        st.get("stage"),
                        st.get("status"),
                        st.get("reason_code"),
                        str(st.get("detail") or "")[:240],
                    )
        # dump nested oracle receipt if present in receipt_refs payloads
        deb = a.get("delivery_evidence_bundle") or {}
        oracle = deb.get("oracle_receipt") or {}
        if oracle:
            print(
                "oracle_receipt status/keys",
                oracle.get("status"),
                list(oracle.keys())[:20],
            )
            print(
                "oracle snippet",
                json.dumps(
                    {
                        k: oracle.get(k)
                        for k in (
                            "status",
                            "demotion_reason",
                            "oracle_demotion_reason",
                            "activation_receipt",
                            "reason_code",
                            "assertions",
                        )
                        if k in oracle
                    },
                    ensure_ascii=False,
                )[:2000],
            )
        break

# compensates relations in BIR
def find_bir(obj, depth=0):
    if depth > 5:
        return None
    if isinstance(obj, dict):
        if isinstance(obj.get("operations"), list) and "actors" in obj:
            return obj
        for v in obj.values():
            r = find_bir(v, depth + 1)
            if r:
                return r
    return None


bir = find_bir(d["full_result"])
rels = [r for r in bir.get("relations", []) if isinstance(r, dict)]
comp = [r for r in rels if r.get("relation_type") == "compensates"]
print("\ncompensates relations:", len(comp))
for r in comp[:40]:
    print(r)

# map NRW primary ops cleanup_requirement from selected obligations if present
# look in campaign/selected experiments
print("\nNRW reason_detail unique:")
details = Counter()
for a in attempts:
    if a.get("reason_code") == "BLOCKED_NON_REVERSIBLE_WRITE":
        details[str(a.get("reason_detail"))] += 1
print(details)
