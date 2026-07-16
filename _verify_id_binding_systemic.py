"""Verify the systemic 'id' binding gap and its link to NON_REVERSIBLE_WRITE.
1) Across all 129 attempts, group runtime_read_binding fixtures by target +
   resolver_path/status to show which bindings have NO resolver systemically.
2) For the 36 NON_REVERSIBLE_WRITE obligations, check whether their endpoints
   need the 'id' binding (DELETE /api/cart/items/:id as probe OR as cleanup
   compensating a POST /api/cart/items create)."""
import json, re
from collections import Counter, defaultdict
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

# 1) runtime_read_binding fixtures across all attempts: target + resolver situation
target_status = defaultdict(Counter)
target_resolver = defaultdict(Counter)
for a in attempts:
    bundle = a.get("delivery_evidence_bundle") or {}
    for rec in bundle.get("contract_evidence_receipts", []):
        if rec.get("kind") == "fixture":
            ev = rec.get("evidence") or {}
            if ev.get("fixture_kind") == "runtime_read_binding":
                # target is encoded in binding_detail like runtime_read_binding_unresolved:<target>
                detail = ev.get("binding_detail") or ""
                tgt = detail.split("runtime_read_binding_unresolved:")[-1].split(":")[0] if "runtime_read_binding_unresolved:" in detail else "?"
                if not tgt:
                    tgt = ev.get("binding_detail") or "?"
                target_status[tgt][rec.get("status")] += 1
                target_resolver[tgt]["resolver_path_empty" if not ev.get("resolver_path") else "resolver_path_set"] += 1
print("=== runtime_read_binding targets across all 129 attempts ===")
for tgt in sorted(target_status):
    print(f"  target='{tgt}': status={dict(target_status[tgt])} resolver={dict(target_resolver[tgt])}")

# Also check binding_materialization_receipts if present (richer resolver info)
bmr_targets = defaultdict(Counter)
for a in attempts:
    blob = a.get("delivery_evidence_bundle") or {}
    # binding materialization receipts may be nested elsewhere; try operational_receipt
    op = a.get("operational_receipt") or {}
    for bmr in (op.get("binding_materialization_receipts") or []):
        tgt = bmr.get("target") or "?"
        bmr_targets[tgt][bmr.get("status")] += 1
print("\n=== binding_materialization_receipts (operational) by target ===")
for tgt in sorted(bmr_targets):
    print(f"  target='{tgt}': {dict(bmr_targets[tgt])}")

# 2) NON_REVERSIBLE_WRITE: do their endpoints need 'id'?
nr = [a for a in attempts if a.get("reason_code") == "BLOCKED_NON_REVERSIBLE_WRITE"]
print(f"\n=== {len(nr)} NON_REVERSIBLE_WRITE obligations ===")
needs_id = 0
endpoint_needs = Counter()
for a in nr:
    locs = [s.get("locator","") for s in a.get("source_refs",[]) if s.get("kind")=="api_operation"]
    needs = any("/:id" in l or "{id}" in l for l in locs)
    if needs:
        needs_id += 1
    for l in locs:
        endpoint_needs[("needs_id" if ("/:id" in l) else "no_id", l)] += 1
print(f"  obligations whose endpoints contain :id  -> {needs_id}/{len(nr)}")
print("  endpoint x needs_id breakdown:")
for (nid, l), c in endpoint_needs.most_common(12):
    print(f"    [{nid}] x{c}: {l}")

# 3) For POST /api/cart/items (create) obligations: is there a declared DELETE compensator in source_refs of ANY attempt?
print("\n=== Does source declare DELETE /api/cart/items/:id (compensator for POST create)? ===")
has_delete_cart = any(
    any(s.get("locator")=="DELETE /api/cart/items/:id" for s in a.get("source_refs",[]))
    for a in attempts
)
print(f"  DELETE /api/cart/items/:id appears in some attempt's source_refs: {has_delete_cart}")
