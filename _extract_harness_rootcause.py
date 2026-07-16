"""Extract the 15 CONTRACT_ORACLE_HARNESS_FAILED attempts and their underlying
contract/observer receipt failure detail from full.json.

Goal: prove whether the harness failures are real evidence-capture bugs
(control/treatment/fixture receipts genuinely FAILED) or governance over-blocks,
and surface the underlying error text that the activation receipt discards.
"""
import json
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

def find_harness_failed_attempts():
    out = []
    for i, a in enumerate(attempts):
        blob = json.dumps(a, ensure_ascii=False)
        if "CONTRACT_ORACLE_HARNESS_FAILED" in blob:
            out.append(i)
    return out

idxs = find_harness_failed_attempts()
print(f"TOTAL attempts: {len(attempts)}")
print(f"HARNESS_FAILED (CONTRACT_ORACLE_HARNESS_FAILED) attempt count: {len(idxs)}")
print("=" * 80)

def short_evidence(ev):
    if not isinstance(ev, dict):
        return ev
    # keep it readable, drop huge nested blobs
    r = {}
    for k, v in ev.items():
        s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
        if isinstance(s, str) and len(s) > 600:
            s = s[:600] + "...[truncated]"
        r[k] = s
    return r

for i in idxs:
    a = attempts[i]
    aid = a.get("attempt_id") or a.get("id") or f"attempt[{i}]"
    oid = a.get("obligation_id") or (a.get("obligation") or {}).get("id")
    bundle = a.get("delivery_evidence_bundle") or {}
    oracle = bundle.get("oracle_receipt") or {}
    activation = oracle.get("activation_receipt") or {}
    oracle_status = oracle.get("status")
    print(f"\n### attempt[{i}]  attempt_id={aid}  obligation_id={oid}")
    print(f"    oracle.status            = {oracle_status}")
    print(f"    activation.status        = {activation.get('status')}")
    print(f"    activation.reason_codes  = {activation.get('reason_codes')}")

    # control / treatment / fixture contract receipts
    ce = bundle.get("contract_evidence_receipts") or []
    for rec in ce:
        kind = rec.get("kind")
        if kind in ("control", "treatment", "fixture"):
            sid = rec.get("subject_id")
            st = rec.get("status")
            ev = rec.get("evidence") or {}
            print(f"    [{kind}:{sid}] status={st}")
            sev = short_evidence(ev)
            for k, v in sev.items():
                print(f"        evidence.{k} = {v}")
    # observers that FAILED / INDETERMINATE / BLOCKED
    obs = bundle.get("observer_receipts") or []
    for rec in obs:
        st = rec.get("status")
        if st in ("FAILED", "INDETERMINATE", "BLOCKED", "UNSUPPORTED"):
            oid_ = rec.get("observer_id")
            ev = rec.get("evidence") or {}
            print(f"    [observer:{oid_}] status={st}")
            sev = short_evidence(ev)
            for k, v in sev.items():
                print(f"        evidence.{k} = {v}")
print("\n" + "=" * 80)
print("DONE")
