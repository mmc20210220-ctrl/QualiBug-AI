"""Analyze 9 findings in detail."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

d = json.loads(Path("_scan_result_latest.json").read_text("utf-8", errors="replace"))
findings = d.get("findings", [])

print(f"=== {len(findings)} FINDINGS DETAIL ===\n")
for i, f in enumerate(findings):
    ev = f.get("evidence") or {}
    oracle = f.get("oracle") or {}
    fa = (f.get("failed_assertions") or [{}])[0]
    print(f"[{i}] {f.get('title','')[:75]}")
    print(f"    expected={f.get('expected')} actual={f.get('actual')}")
    print(f"    control_succeeded={ev.get('control_succeeded')}")
    print(f"    assertion: kind={fa.get('kind')} expected={fa.get('expected')} actual={fa.get('actual')} status={fa.get('status')}")
    print(f"    oracle: status={oracle.get('status')} verdict={oracle.get('verdict')}")
    print(f"    risk_family={f.get('risk_family')} category={f.get('category')}")
    print(f"    description: {f.get('description','')[:100]}")
    # Check reproduction steps
    repro = f.get("reproduction_steps") or ev.get("reproduction_steps") or []
    if repro:
        print(f"    repro: {repro[0] if repro else '?'}")
    print()

# Pipeline health summary
ph = d.get("pipeline_health") or {}
print("=== PIPELINE HEALTH ===")
print(f"  selected: {ph.get('selected_obligation_count')}")
print(f"  executed: {ph.get('executed_obligation_count')}")
print(f"  blocked: {ph.get('blocked_obligation_count')}")
print(f"  harness_failed: {ph.get('harness_failure_count')}")
print(f"  deliverables: {ph.get('formal_customer_deliverable_count')}")
print(f"  terminal_reasons: {ph.get('operator_note','')[:300]}")

# Discovery funnel
funnel = d.get("discovery_funnel") or {}
print(f"\n=== FUNNEL ===")
print(f"  candidates: {funnel.get('candidate_count')}")
print(f"  canonical_defects: {funnel.get('canonical_defect_count')}")
print(f"  validated_bugs: {funnel.get('validated_bug_count')}")
