"""Check obligation plan details."""
import json
from pathlib import Path

ROOT = Path(r"D:\QualiBug-AI\QualiBug-AI-main")
scan = json.loads((ROOT / "platform_outputs/warehouse_e/scan_result.json").read_text(encoding="utf-8"))
v12 = scan.get("v12", {})

# Obligation plan
obl_plan = v12.get("obligation_plan", {})
print(f"obligation_plan keys: {sorted(obl_plan.keys())[:20]}")
print(f"budget: {obl_plan.get('budget')}")
print(f"selected_count: {obl_plan.get('selected_count')}")
print(f"pending_count: {obl_plan.get('pending_count')}")
print(f"stop_condition: {obl_plan.get('stop_condition')}")
selected = obl_plan.get("selected", [])
print(f"selected list len: {len(selected)}")
pending = obl_plan.get("pending_next_round", [])
print(f"pending list len: {len(pending)}")

# Experiment compile
exp_compile = v12.get("experiment_compile", {})
print(f"\nexperiment_compile: {json.dumps(exp_compile, ensure_ascii=False)[:500]}")

# Experiment execution
exp_exec = v12.get("experiment_execution", {})
print(f"\nexperiment_execution: {json.dumps(exp_exec, ensure_ascii=False)[:500]}")

# Agent intent plan
agent_plan = v12.get("agent_intent_plan", {})
print(f"\nagent_intent_plan keys: {sorted(agent_plan.keys())[:15]}")
intents = agent_plan.get("intents", [])
print(f"intents count: {len(intents)}")
if intents:
    print(f"first intent: {json.dumps(intents[0], ensure_ascii=False)[:300]}")

# Discovery funnel
funnel = v12.get("discovery_funnel", {})
print(f"\ndiscovery_funnel: {json.dumps(funnel, ensure_ascii=False)[:800]}")

# Check test_obligations
test_obls = v12.get("test_obligations", [])
print(f"\ntest_obligations: {len(test_obls)}")
if test_obls:
    statuses = {}
    for t in test_obls:
        if isinstance(t, dict):
            s = t.get("compile_status", t.get("status", "?"))
            statuses[s] = statuses.get(s, 0) + 1
    print(f"  statuses: {statuses}")
