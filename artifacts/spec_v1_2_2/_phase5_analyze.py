"""Phase 5-17: Comprehensive analysis of V1.2.2 Live Run results."""
import json
from pathlib import Path
from collections import Counter

ARTIFACT_DIR = Path("artifacts/spec_v1_2_2")
result = json.load(open(ARTIFACT_DIR / "v122_live_scan_result_raw.json", encoding="utf-8"))
v = result.get("v12", {})

# ═══ Phase 5: Obligation Funnel ═══
ledger = v.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"{'='*60}")
print(f"[Phase 5] Obligation Funnel Ledger")
print(f"{'='*60}")
print(f"  Total attempts: {len(attempts)}")
print(f"  Selected: {ledger.get('selected_count')}")
print(f"  Terminal: {ledger.get('terminal_count')}")
print(f"  Complete: {ledger.get('complete')}")
print(f"  Terminal status counts: {json.dumps(ledger.get('terminal_status_counts', {}))}")

# Sample attempt structure
if attempts:
    sample = attempts[0]
    print(f"\n  Sample attempt keys: {sorted(sample.keys())}")
    # Count by status
    status_counts = Counter(a.get("status", "?") for a in attempts)
    print(f"  Status distribution: {dict(status_counts)}")
    # Count by terminal_reason
    reason_counts = Counter(a.get("reason_code", a.get("terminal_reason", "?")) for a in attempts)
    print(f"  Reason codes:")
    for reason, count in reason_counts.most_common(15):
        print(f"    {reason}: {count}")

# ═══ Phase 6: Experiment Funnel ═══
print(f"\n{'='*60}")
print(f"[Phase 6] Experiment Funnel Ledger")
print(f"{'='*60}")
exec_data = v.get("experiment_execution", {})
results = exec_data.get("results", [])
print(f"  Scheduled: {exec_data.get('scheduled_count')}")
print(f"  Selected: {exec_data.get('selected_count')}")
print(f"  Executed: {exec_data.get('executed_count')}")
print(f"  Blocked: {exec_data.get('blocked_count')}")
print(f"  Harness failures: {exec_data.get('harness_failure_count')}")
print(f"  Cleanup failures: {exec_data.get('cleanup_failures')}")
print(f"  Every experiment has receipt: {exec_data.get('every_experiment_has_receipt')}")
print(f"  Results in output: {len(results)}")

# Experiment status/reason distribution
exp_statuses = Counter()
exp_reasons = Counter()
for res in results:
    st = res.get("status", res.get("terminal_status", "?"))
    exp_statuses[st] += 1
    reason = res.get("blocking_reason", res.get("terminal_reason", res.get("reason", "")))
    if reason:
        exp_reasons[reason] += 1

print(f"\n  Experiment status distribution: {dict(exp_statuses)}")
print(f"  Experiment blocking reasons:")
for reason, count in exp_reasons.most_common(15):
    print(f"    {reason}: {count}")

# Sample experiment
if results:
    print(f"\n  Sample experiment keys: {sorted(results[0].keys())[:20]}")

# ═══ Discovery Funnel (full) ═══
print(f"\n{'='*60}")
print(f"[Funnel] Discovery Funnel Stages")
print(f"{'='*60}")
funnel = v.get("discovery_funnel", {})
stages = funnel.get("stages", [])
for s in stages:
    name = s.get("name", "?")
    inp = s.get("input", 0)
    succ = s.get("success", 0)
    blk = s.get("blocked", 0)
    fail = s.get("failed", 0)
    reasons = s.get("reason_counts", {})
    print(f"  {name}: in={inp} pass={succ} block={blk} fail={fail}")
    for rk, rv in sorted(reasons.items(), key=lambda x: -x[1])[:5]:
        print(f"      {rk}: {rv}")

# ═══ Findings ═══
print(f"\n{'='*60}")
print(f"[Phase 9] Findings Classification")
print(f"{'='*60}")
findings = v.get("findings", [])
print(f"  Total findings: {len(findings)}")
for i, f in enumerate(findings):
    title = str(f.get("title", f.get("name", "?")))[:100]
    sev = f.get("severity", "?")
    delivery = f.get("delivery_status", f.get("status", "?"))
    oracle = f.get("oracle_result", "?")
    print(f"  [{i+1}] sev={sev} delivery={delivery} oracle={oracle}")
    print(f"      {title}")

# Candidate findings
candidates = v.get("candidate_findings", [])
print(f"\n  Candidate findings: {len(candidates) if isinstance(candidates, list) else 'dict'}")

# Formal delivery
formal = v.get("formal_delivery_authority", {})
print(f"\n  Formal delivery authority:")
if isinstance(formal, dict):
    print(f"    keys: {sorted(formal.keys())[:10]}")
    print(f"    formal_count: {formal.get('formal_count', formal.get('formal_finding_count', '?'))}")

# Canonical defect registry
cdr = v.get("canonical_defect_registry", {})
print(f"\n  Canonical defect registry:")
if isinstance(cdr, dict):
    print(f"    canonical_defect_count: {cdr.get('canonical_defect_count', '?')}")
    print(f"    delivery_occurrence_count: {cdr.get('delivery_occurrence_count', '?')}")

# ═══ Experiment Compile ═══
print(f"\n{'='*60}")
print(f"[Compile] Experiment Compile Details")
print(f"{'='*60}")
compile_data = v.get("experiment_compile", {})
if isinstance(compile_data, dict):
    print(f"  keys: {sorted(compile_data.keys())[:15]}")
    print(f"  total: {compile_data.get('total', '?')}")
    print(f"  compiled: {compile_data.get('compiled', '?')}")
    print(f"  blocked: {compile_data.get('blocked', '?')}")

# ═══ Trace Ledger ═══
print(f"\n{'='*60}")
print(f"[Phase 8] Trace Ledger / Runtime Binding")
print(f"{'='*60}")
trace = v.get("trace_ledger", {})
if isinstance(trace, dict):
    print(f"  keys: {sorted(trace.keys())[:10]}")
    entries = trace.get("entries", trace.get("traces", []))
    print(f"  entries: {len(entries) if isinstance(entries, list) else '?'}")
elif isinstance(trace, list):
    print(f"  list entries: {len(trace)}")

# Runtime contract
rc = v.get("runtime_contract", {})
print(f"\n  Runtime contract:")
if isinstance(rc, dict):
    print(f"    status: {rc.get('status')}")
    print(f"    execution_mode: {rc.get('execution_mode')}")
    print(f"    environment_ref: {rc.get('environment_ref')}")
    print(f"    approved_base_url: {rc.get('approved_base_url')}")

# ═══ Mainline Run ═══
print(f"\n{'='*60}")
print(f"[Mainline] Run Identity")
print(f"{'='*60}")
mainline = v.get("mainline_run", {})
print(f"  run_id: {mainline.get('run_id')}")
print(f"  campaign_id: {mainline.get('campaign_id')}")
print(f"  mainline_authority: {mainline.get('mainline_authority')}")
print(f"  target_base_url: {mainline.get('target_base_url')}")

# ═══ Test Obligations ═══
print(f"\n{'='*60}")
print(f"[Obligations] Test Obligation Plan")
print(f"{'='*60}")
obligations = v.get("test_obligations", {})
if isinstance(obligations, dict):
    obl_list = obligations.get("obligations", [])
    print(f"  Total obligations: {len(obl_list)}")
elif isinstance(obligations, list):
    print(f"  Total obligations: {len(obligations)}")

# Obligation plan
obl_plan = v.get("obligation_plan", {})
if isinstance(obl_plan, dict):
    print(f"  Plan keys: {sorted(obl_plan.keys())[:10]}")
    plan_items = obl_plan.get("items", obl_plan.get("obligations", []))
    print(f"  Plan items: {len(plan_items) if isinstance(plan_items, list) else '?'}")

print(f"\n{'='*60}")
print(f"[DONE] Analysis complete.")
print(f"{'='*60}")
