"""Check payment_requests in before/after observations."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_execution result
exp_exec = v12.get("experiment_execution", {})
results = exp_exec.get("results", [])
cons_results = [r for r in results if r.get("obligation_id") == "obl_0413ca640355cba0d746"]

if cons_results:
    res = cons_results[0]
    steps = res.get("steps", [])
    
    for step in steps[:1]:
        gov = step.get("governance_receipt", {})
        if gov:
            before = gov.get("before", {})
            after = gov.get("after", {})
            
            # Check before body
            before_body = before.get("body", [])
            print(f"Before body type: {type(before_body).__name__}")
            if isinstance(before_body, list):
                print(f"Before body count: {len(before_body)}")
                if before_body:
                    first = before_body[0]
                    print(f"First record keys: {list(first.keys())[:15]}")
                    print(f"First record milestone_id: {first.get('milestone_id')}")
                    print(f"First record amount: {first.get('amount')}")
                    
                    # Collect all unique milestone_ids
                    milestone_ids = set()
                    for rec in before_body:
                        mid = rec.get("milestone_id")
                        if mid:
                            milestone_ids.add(mid)
                    print(f"\nUnique milestone_ids in before: {len(milestone_ids)}")
                    for mid in list(milestone_ids)[:5]:
                        print(f"  - {mid}")
            
            # Check after body
            after_body = after.get("body", [])
            print(f"\nAfter body type: {type(after_body).__name__}")
            if isinstance(after_body, list):
                print(f"After body count: {len(after_body)}")
