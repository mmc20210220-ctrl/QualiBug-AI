"""Deep bottleneck dive: characterize the 75 blocked obligations by reason,
endpoint, write method, risk_family, and reason_detail — to pinpoint whether the
block is concentrated (few operations) or diffuse, and whether it's a source-doc
gap (missing compensating action / observer) vs governance over-block."""
import json, re
from collections import Counter, defaultdict
from pathlib import Path

path = Path(r"D:\QualiBug-AI\QualiBug-AI-main\_funnel_runs\full.json")
d = json.load(open(path, encoding="utf-8"))
attempts = d["full_result"]["obligation_attempt_ledger"]["attempts"]

def locators(a):
    out = []
    for s in a.get("source_refs", []):
        loc = s.get("locator") or ""
        if s.get("kind") == "api_operation" and loc:
            out.append(loc)
    return out

def methods(locs):
    ms = []
    for l in locs:
        m = re.match(r"^(GET|POST|PUT|PATCH|DELETE)\b", l)
        if m:
            ms.append(m.group(1))
    return ms

blocked = [a for a in attempts if a.get("terminal_status") == "BLOCKED"]
print(f"blocked total: {len(blocked)}")
by_reason = defaultdict(list)
for a in blocked:
    by_reason[a.get("reason_code")].append(a)

for reason, items in sorted(by_reason.items(), key=lambda x: -len(x[1])):
    print("\n" + "=" * 78)
    print(f"{reason}  x{len(items)}")
    print("=" * 78)
    # reason_detail variants
    rd = Counter(a.get("reason_detail") or "(none)" for a in items)
    print("  reason_detail variants:")
    for v, c in rd.most_common(8):
        print(f"    x{c}: {v[:90]}")
    # terminal_stage
    ts = Counter(a.get("terminal_stage") for a in items)
    print("  terminal_stage:", dict(ts))
    # risk_family
    rf = Counter(a.get("risk_family") for a in items)
    print("  risk_family:", dict(rf))
    # locators (api operations)
    loc_counter = Counter()
    method_counter = Counter()
    for a in items:
        ls = locators(a)
        for l in ls:
            loc_counter[l] += 1
        for m in methods(ls):
            method_counter[m] += 1
    print("  write methods:", dict(method_counter))
    print("  top endpoints:")
    for l, c in loc_counter.most_common(10):
        print(f"    x{c}: {l}")
    # distinct obligation operation_refs count
    print(f"  distinct operation_refs: {len(set(sum([a.get('operation_refs',[]) for a in items],[])))}")

# Also: for NON_REVERSIBLE_WRITE, is the blocked write the SETUP or the probe?
# Check operational_receipt / stages for write-class hints.
print("\n" + "=" * 78)
print("NON_REVERSIBLE_WRITE — are these read-probe obligations needing a setup write,")
print("or write-probe obligations themselves? (method of the probe endpoint above)")
print("=" * 78)

# Cross-check: do the NON_REVERSIBLE blocked endpoints have a declared DELETE in source?
nr = by_reason.get("BLOCKED_NON_REVERSIBLE_WRITE", [])
nr_locs = set()
for a in nr:
    for l in locators(a):
        nr_locs.add(l)
print("distinct endpoints under NON_REVERSIBLE_WRITE:")
for l in sorted(nr_locs):
    print(f"  {l}")

# MISSING_OBSERVER: what observer is missing? look in reason_detail / stages
mo = by_reason.get("BLOCKED_MISSING_OBSERVER", [])
print("\nMISSING_OBSERVER — reason_detail (may name the missing observer):")
rd_mo = Counter(a.get("reason_detail") or "(none)" for a in mo)
for v, c in rd_mo.most_common(10):
    print(f"  x{c}: {v[:100]}")
