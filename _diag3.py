"""Check how many obligations have COMPILED experiments."""
import json
from pathlib import Path

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
scan = json.loads((ROOT / "platform_outputs/warehouse_e/scan_result.json").read_text(encoding="utf-8"))

print("Top-level keys:", sorted(scan.keys()))
v12 = scan.get("v12", {})
print("V12 keys:", sorted(v12.keys()))

# Check experiment_pack
exp_pack = v12.get("experiment_pack", {})
print(f"\nexperiment_pack type: {type(exp_pack).__name__}")
if isinstance(exp_pack, dict):
    print(f"  keys: {sorted(exp_pack.keys())[:20]}")
    by_obl = exp_pack.get("by_obligation", {})
    print(f"  by_obligation count: {len(by_obl)}")
    compiled_count = 0
    blocked_count = 0
    other_count = 0
    for v in by_obl.values():
        if isinstance(v, dict):
            cr = v.get("compile_receipt", {}) or {}
            status = (cr.get("status") or "").upper()
            if status == "COMPILED":
                compiled_count += 1
            elif "BLOCKED" in status:
                blocked_count += 1
            else:
                other_count += 1
    print(f"  COMPILED: {compiled_count}")
    print(f"  BLOCKED: {blocked_count}")
    print(f"  OTHER: {other_count}")

# Check experiments key
experiments = v12.get("experiments", {})
print(f"\nexperiments type: {type(experiments).__name__}, count: {len(experiments)}")
if isinstance(experiments, dict):
    by_obl2 = experiments.get("by_obligation", {})
    print(f"  by_obligation count: {len(by_obl2)}")

# Check obligation_attempt_ledger
ledger = scan.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"\nLedger attempts: {len(attempts)}")
print(f"  selected_count: {ledger.get('selected_count')}")
print(f"  terminal_count: {ledger.get('terminal_count')}")
print(f"  terminal_status_counts: {ledger.get('terminal_status_counts')}")

# Check how many attempts have experiment_id
with_exp = [a for a in attempts if a.get("experiment_id")]
print(f"  with experiment_id: {len(with_exp)}")
without_exp = [a for a in attempts if not a.get("experiment_id")]
print(f"  without experiment_id: {len(without_exp)}")

# Check the obligation_generation phase
obl_gen = v12.get("phases", {}).get("obligation_generation", {})
print(f"\nObligation generation: {json.dumps(obl_gen, ensure_ascii=False)[:500]}")

# Check agent_intent phase
agent_intent = v12.get("phases", {}).get("agent_intent", {})
print(f"\nAgent intent: {json.dumps(agent_intent, ensure_ascii=False)[:500]}")

# Check if there's a planning_budget_receipt
budget_receipt = scan.get("planning_budget_receipt", v12.get("planning_budget_receipt", {}))
print(f"\nBudget receipt: {json.dumps(budget_receipt, ensure_ascii=False)[:500]}")

# Check campaign info
campaign = scan.get("campaign", v12.get("campaign", {}))
print(f"\nCampaign: {json.dumps(campaign, ensure_ascii=False)[:500]}")
