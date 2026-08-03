"""Deep diagnosis of why blind run produced 0 findings."""
import json
from pathlib import Path

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
scan = json.loads((ROOT / "platform_outputs/warehouse_e/scan_result.json").read_text(encoding="utf-8"))

ledger = scan.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])

# 1. Gate-blocked experiments (actually executed but oracle/gate rejected)
blocked_gate = [a for a in attempts if a.get("reason_code") == "CONTRACT_ORACLE_BLOCKED"]
print(f"=== CONTRACT_ORACLE_BLOCKED ({len(blocked_gate)}) ===")
for a in blocked_gate[:3]:
    print(f"  ID: {a['candidate_id']}")
    print(f"  obs_receipts: {len(a.get('observation_receipt_ids', []))}")
    print(f"  oracle: {a.get('oracle_receipt_id')}")
    print(f"  gate: {a.get('gate_receipt_id')}")
    locators = [s.get("locator") for s in a.get("source_refs", [])]
    print(f"  locators: {locators}")
    print()

# 2. Missing operation
blocked_op = [a for a in attempts if a.get("reason_code") == "BLOCKED_MISSING_OPERATION"]
print(f"=== BLOCKED_MISSING_OPERATION ({len(blocked_op)}) ===")
for a in blocked_op[:3]:
    print(f"  ID: {a['candidate_id']}")
    print(f"  operation_refs: {a.get('operation_refs')}")
    locators = [s.get("locator") for s in a.get("source_refs", [])]
    print(f"  locators: {locators}")
    print()

# 3. Missing binding
blocked_bind = [a for a in attempts if a.get("reason_code") == "BLOCKED_MISSING_BINDING"]
print(f"=== BLOCKED_MISSING_BINDING ({len(blocked_bind)}) ===")
for a in blocked_bind[:3]:
    print(f"  ID: {a['candidate_id']}")
    locators = [s.get("locator") for s in a.get("source_refs", [])]
    print(f"  locators: {locators}")
    print()

# 4. Check v12 execution phase details
v12 = scan.get("v12", {})
phases = v12.get("phases", {})
exec_phase = phases.get("execution", {})
print(f"=== Execution Phase Details ===")
for k, v in exec_phase.items():
    if isinstance(v, (str, int, float, bool)):
        print(f"  {k}: {v}")
    elif isinstance(v, list):
        print(f"  {k}: [{len(v)} items]")
    elif isinstance(v, dict):
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")

# 5. Check plan/selection
plan_phase = phases.get("plan", phases.get("planning", {}))
print(f"\n=== Plan Phase ===")
for k, v in plan_phase.items():
    if isinstance(v, (str, int, float, bool)):
        print(f"  {k}: {v}")
    elif isinstance(v, list):
        print(f"  {k}: [{len(v)} items]")
    elif isinstance(v, dict):
        print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")

# 6. Check oracle receipts
oracle_receipts = scan.get("oracle_receipts", [])
print(f"\n=== Oracle Receipts ({len(oracle_receipts)}) ===")
for r in oracle_receipts[:5]:
    if isinstance(r, dict):
        print(f"  {r.get('receipt_id', r.get('oracle_receipt_id', '?'))}: verdict={r.get('verdict')}, reason={r.get('reason_code', r.get('reason', ''))}")

# 7. Check gate receipts
gate_receipts = scan.get("gate_receipts", [])
print(f"\n=== Gate Receipts ({len(gate_receipts)}) ===")
for r in gate_receipts[:5]:
    if isinstance(r, dict):
        print(f"  {r.get('receipt_id', r.get('gate_receipt_id', '?'))}: decision={r.get('decision')}, reason={r.get('reason_code', r.get('reason', ''))}")

# 8. Check findings/candidates
findings = scan.get("findings", [])
candidates = scan.get("candidate_findings", [])
print(f"\n=== Findings: {len(findings)}, Candidates: {len(candidates)} ===")
for c in candidates[:3]:
    if isinstance(c, dict):
        print(f"  {c.get('candidate_id', '?')}: {c.get('title', '')[:80]}")

# 9. Check experiment results
experiments = scan.get("experiments", scan.get("experiment_results", []))
print(f"\n=== Experiments: {len(experiments)} ===")
if isinstance(experiments, list):
    for e in experiments[:5]:
        if isinstance(e, dict):
            print(f"  {e.get('experiment_id', '?')}: status={e.get('status')}, findings={e.get('finding_count', 0)}")
elif isinstance(experiments, dict):
    for eid, e in list(experiments.items())[:5]:
        print(f"  {eid}: {json.dumps(e, ensure_ascii=False)[:150]}")

# 10. Check observations
observations = scan.get("observations", [])
print(f"\n=== Observations: {len(observations)} ===")
if isinstance(observations, list):
    for o in observations[:3]:
        if isinstance(o, dict):
            print(f"  {o.get('observation_id', '?')}: status={o.get('status_code', o.get('status'))}, method={o.get('method')}, path={o.get('path', o.get('url', ''))[:60]}")
