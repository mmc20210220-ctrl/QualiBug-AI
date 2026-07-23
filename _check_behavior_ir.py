"""Check Behavior IR operations for payment_requests."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check behavior_ir
behavior_ir = v12.get("behavior_ir", {})
operations = behavior_ir.get("operations", [])
entities = behavior_ir.get("entities", [])

print(f"Total operations: {len(operations)}")
print(f"Total entities: {len(entities)}")

# Find payment_requests entity
pr_entities = [e for e in entities if "payment" in str(e.get("name", "")).lower()]
print(f"\nPayment-related entities: {len(pr_entities)}")
for e in pr_entities[:3]:
    print(f"  - {e.get('name')}: id={e.get('id')}")

# Find GET operations for payment_requests
pr_ops = [op for op in operations if "payment" in str(op.get("path", "")).lower() and str(op.get("method", "")).upper() == "GET"]
print(f"\nGET operations for payment*: {len(pr_ops)}")
for op in pr_ops[:5]:
    print(f"  - {op.get('method')} {op.get('path')}: id={op.get('id')}")

# Find milestones entity
ms_entities = [e for e in entities if "milestone" in str(e.get("name", "")).lower()]
print(f"\nMilestone-related entities: {len(ms_entities)}")
for e in ms_entities[:3]:
    print(f"  - {e.get('name')}: id={e.get('id')}")

# Find GET operations for milestones
ms_ops = [op for op in operations if "milestone" in str(op.get("path", "")).lower() and str(op.get("method", "")).upper() == "GET"]
print(f"\nGET operations for milestone*: {len(ms_ops)}")
for op in ms_ops[:5]:
    print(f"  - {op.get('method')} {op.get('path')}: id={op.get('id')}")

# Check relations
relations = behavior_ir.get("relations", [])
print(f"\nTotal relations: {len(relations)}")
pr_rels = [r for r in relations if "payment" in str(r.get("from_ref", "")).lower() or "payment" in str(r.get("to_ref", "")).lower()]
print(f"Payment-related relations: {len(pr_rels)}")
for r in pr_rels[:3]:
    print(f"  - {r.get('from_ref')} -> {r.get('to_ref')}: type={r.get('relation_type')}")
