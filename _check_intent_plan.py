"""Check if conservation is in agent_intent_plan."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_compile
exp_compile = v12.get("experiment_compile", {})
agent_intent_plan = exp_compile.get("agent_intent_plan", {})
intents = agent_intent_plan.get("intents", [])
print(f"agent_intent_plan intents count: {len(intents)}")

# Get conservation obligation_id
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]

if cons_attempts:
    cons_obl_id = cons_attempts[0].get("obligation_id")
    print(f"\nConservation obligation_id: {cons_obl_id}")
    
    # Check if conservation is in intents
    cons_intents = [i for i in intents if i.get("obligation_id") == cons_obl_id]
    print(f"Conservation in intents: {len(cons_intents)}")
    
    # Check obligation_plan
    obligation_plan = exp_compile.get("obligation_plan", {})
    selected = obligation_plan.get("selected", [])
    pending = obligation_plan.get("pending_next_round", [])
    print(f"\nobligation_plan.selected count: {len(selected)}")
    print(f"obligation_plan.pending_next_round count: {len(pending)}")
    
    cons_selected = [s for s in selected if s.get("obligation_id") == cons_obl_id]
    cons_pending = [p for p in pending if p.get("obligation_id") == cons_obl_id]
    print(f"Conservation in selected: {len(cons_selected)}")
    print(f"Conservation in pending: {len(cons_pending)}")
    
    # Check all conservation obligations
    cons_obl_ids = [a.get("obligation_id") for a in cons_attempts]
    print(f"\nAll conservation obligation_ids: {cons_obl_ids[:5]}")
    
    # Check if any conservation is in intents
    cons_in_intents = [i for i in intents if i.get("obligation_id") in cons_obl_ids]
    print(f"Conservation obligations in intents: {len(cons_in_intents)}")
    
    # Check if any conservation is in selected
    cons_in_selected = [s for s in selected if s.get("obligation_id") in cons_obl_ids]
    print(f"Conservation obligations in selected: {len(cons_in_selected)}")
