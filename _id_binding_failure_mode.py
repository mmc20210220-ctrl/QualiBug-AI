"""Final piece: distinct binding_detail values for the 15 failed 'id' runtime
bindings — to see whether the resolver returned an HTTP error, no response, or
couldn't extract the value. This pinpoints the resolver failure mode."""
import json
from collections import Counter
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

detail_counter = Counter()
samples = {}
for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    for rec in bundle.get("contract_evidence_receipts", []):
        if rec.get("kind") == "fixture" and rec.get("status") == "FAILED":
            ev = rec.get("evidence") or {}
            detail = ev.get("binding_detail") or "(no detail)"
            detail_counter[detail] += 1
            if detail not in samples:
                samples[detail] = {
                    "resolver_path": ev.get("resolver_path"),
                    "resolver_status_code": ev.get("resolver_status_code"),
                    "binding_reason_code": ev.get("binding_reason_code"),
                }

print("=== distinct binding_detail for FAILED runtime_read_binding fixtures (15) ===")
for detail, c in detail_counter.most_common():
    print(f"\n  x{c}: {detail}")
    print(f"     {samples[detail]}")

# Also: for the resolver_path_set cases, what endpoints are the resolvers hitting?
print("\n=== resolver paths used by FAILED id bindings ===")
rp_counter = Counter()
for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    for rec in bundle.get("contract_evidence_receipts", []):
        if rec.get("kind") == "fixture" and rec.get("status") == "FAILED":
            ev = rec.get("evidence") or {}
            rp = ev.get("resolver_path") or "(empty)"
            rp_counter[(rp, ev.get("resolver_status_code"))] += 1
for (rp, sc), c in rp_counter.most_common():
    print(f"  x{c}: resolver_path={rp!r} status_code={sc}")
