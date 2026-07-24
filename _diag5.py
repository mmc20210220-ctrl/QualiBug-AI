"""Check binding resolution failure details."""
import json
from pathlib import Path

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
scan = json.loads((ROOT / "platform_outputs/warehouse_e/scan_result.json").read_text(encoding="utf-8"))
v12 = scan.get("v12", {})

# Check experiment execution details
exp_exec = v12.get("experiment_execution", {})
print(f"experiment_execution keys: {sorted(exp_exec.keys())[:20]}")

# Check execution trace summaries
traces = v12.get("execution_trace_summaries", [])
print(f"\nexecution_trace_summaries: {len(traces)}")
for t in traces[:3]:
    if isinstance(t, dict):
        print(f"  {t.get('experiment_id', '?')}: status={t.get('status')}, reason={t.get('reason_code', '')}")
        binding_receipts = t.get("binding_materialization_receipts", [])
        if binding_receipts:
            print(f"    binding_receipts: {json.dumps(binding_receipts[:2], ensure_ascii=False)[:300]}")

# Check the obligation_attempt_ledger for BLOCKED_MISSING_BINDING details
ledger = scan.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
blocked_binding = [a for a in attempts if a.get("reason_code") == "BLOCKED_MISSING_BINDING"]
print(f"\nBLOCKED_MISSING_BINDING attempts: {len(blocked_binding)}")
for a in blocked_binding[:2]:
    print(f"  ID: {a['candidate_id']}")
    print(f"  experiment_id: {a.get('experiment_id')}")
    print(f"  execution_id: {a.get('execution_id')}")
    print(f"  terminal_stage: {a.get('terminal_stage')}")
    stages = a.get("stages", [])
    for s in stages:
        if isinstance(s, dict):
            print(f"    stage={s.get('stage')}: status={s.get('status')}, reason={s.get('reason_code')}")
    print()

# Check the experiment_compile for blocked experiments
exp_compile = v12.get("experiment_compile", {})
experiments = exp_compile.get("experiments", [])
print(f"\nTotal experiments in compile: {len(experiments)}")
blocked_exps = [e for e in experiments if isinstance(e, dict) and (e.get("compile_receipt", {}) or {}).get("status") == "BLOCKED"]
print(f"BLOCKED experiments: {len(blocked_exps)}")
# Sample blocked experiment
if blocked_exps:
    be = blocked_exps[0]
    print(f"  Sample blocked: {be.get('experiment_id')}")
    print(f"  obligation_id: {be.get('obligation_id')}")
    cr = be.get("compile_receipt", {})
    print(f"  compile_receipt: {json.dumps(cr, ensure_ascii=False)[:300]}")
    bp = be.get("binding_plan", [])
    print(f"  binding_plan: {json.dumps(bp[:2], ensure_ascii=False)[:400]}")

# Check compiled experiments sample
compiled_exps = [e for e in experiments if isinstance(e, dict) and (e.get("compile_receipt", {}) or {}).get("status") == "COMPILED"]
print(f"\nCOMPILED experiments: {len(compiled_exps)}")
if compiled_exps:
    ce = compiled_exps[0]
    print(f"  Sample compiled: {ce.get('experiment_id')}")
    print(f"  obligation_id: {ce.get('obligation_id')}")
    print(f"  risk_family: {ce.get('risk_family')}")
    bp = ce.get("binding_plan", [])
    print(f"  binding_plan len: {len(bp)}")
    if bp:
        print(f"  binding_plan[0]: {json.dumps(bp[0], ensure_ascii=False)[:300]}")
    tp = ce.get("treatment_plan", [])
    if tp:
        print(f"  treatment_plan[0]: {json.dumps(tp[0], ensure_ascii=False)[:300]}")
