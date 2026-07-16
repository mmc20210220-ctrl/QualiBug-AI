"""Extract fixture_setup observability from the fresh full.json: for each failed
runtime_read_binding fixture, show whether fixture_setup was generated, the
blocked_reason, create POST status, and which dependency blocked.
Run AFTER the rerun with the fixture_setup observability instrumentation."""
import json
from collections import Counter
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

reason_counter = Counter()
dep_counter = Counter()
create_status_counter = Counter()
samples = []

for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    for rec in bundle.get("contract_evidence_receipts", []):
        if rec.get("kind") == "fixture" and rec.get("status") == "FAILED":
            ev = rec.get("evidence") or {}
            br = ev.get("fixture_setup_blocked_reason") or "(none)"
            reason_counter[br] += 1
            if ev.get("fixture_setup_dependency"):
                dep_counter[ev.get("fixture_setup_dependency")] += 1
            cs = ev.get("fixture_setup_create_status")
            if cs is not None and cs != 0:
                create_status_counter[cs] += 1
            if len(samples) < 4:
                samples.append({
                    "subject_id": rec.get("subject_id"),
                    "binding_detail": ev.get("binding_detail"),
                    "fixture_setup_generated": ev.get("fixture_setup_generated"),
                    "fixture_setup_blocked_reason": br,
                    "fixture_setup_dependency": ev.get("fixture_setup_dependency"),
                    "fixture_setup_create_path": ev.get("fixture_setup_create_path"),
                    "fixture_setup_create_status": cs,
                    "resolver_path": ev.get("resolver_path"),
                    "resolver_status_code": ev.get("resolver_status_code"),
                })

print(f"=== failed runtime_read_binding fixtures: {sum(reason_counter.values())} ===")
print("\nblocked_reason distribution:")
for r, c in reason_counter.most_common():
    print(f"  x{c}: {r}")
print("\ndependency distribution:")
for dep, c in dep_counter.most_common():
    print(f"  x{c}: {dep}")
print("\ncreate POST status distribution (non-zero):")
for s, c in create_status_counter.most_common():
    print(f"  x{c}: {s}")
print("\nsamples:")
for s in samples:
    print(json.dumps(s, ensure_ascii=False, indent=2))
