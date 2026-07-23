"""Test discover_read_operations for payment_requests."""
import json
from ai_test_asset_center.related_entity_observer_binder import bind_observer_plan, discover_read_operations

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})
bir = v12.get("behavior_ir", {})
ops = bir.get("operations", [])

# Test discover_read_operations
cands = discover_read_operations(
    "payment_requests",
    ops,
    required_fields=["amount"],
    relation_key="milestone_id",
    scope_fields=["tenant_id"],
)
print(f"Candidates: {len(cands)}")
for c in cands[:3]:
    print(f"  path: {c['path']}")
    print(f"  score: {c['score']}")
    print(f"  score_breakdown: {c['score_breakdown']}")
    print(f"  parameter_bindings: {c['parameter_bindings']}")
    print()

# Test bind_observer_plan
observer_reqs = [
    {
        "entity_alias": "root",
        "entity_name": "milestones",
        "entity_id": "bir_fa0ac0d317973e84",
        "required_fields": ["amount"],
        "aggregate_fields": [],
        "scope_fields": ["tenant_id"],
        "identity_fields": ["contract_id", "id"],
        "snapshot": "CURRENT",
        "cardinality": "ONE",
    },
    {
        "entity_alias": "related_a",
        "entity_name": "payment_requests",
        "entity_id": "bir_898bcced3c4c2e2c",
        "required_fields": ["amount"],
        "aggregate_fields": ["amount"],
        "scope_fields": ["tenant_id"],
        "identity_fields": ["contract_id", "id", "idempotency_key", "invoice_id", "milestone_id"],
        "snapshot": "CURRENT",
        "cardinality": "MANY",
        "relation_key": "milestone_id",
        "collection_requirements": {
            "pagination_required": True,
            "deduplicate_by": ["contract_id", "id", "idempotency_key", "invoice_id", "milestone_id"],
            "empty_collection_policy": "INDETERMINATE",
        },
    },
]

plan = bind_observer_plan(
    observer_reqs,
    bir,
    root_identity_value="test_milestone_id_123",
    tenant_scope_values={"tenant_id": "test_tenant"},
)

print(f"\nObserver plan:")
print(f"  root_observer: {plan.get('root_observer') is not None}")
print(f"  related_observers: {len(plan.get('related_observers', []))}")
print(f"  blockers: {len(plan.get('blockers', []))}")

for b in plan.get("blockers", []):
    print(f"    BLOCKER: {b.get('entity_alias')}: {b.get('reason')} - {b.get('detail')}")

for ro in plan.get("related_observers", []):
    print(f"\n  Related observer: {ro.get('entity_alias')}")
    print(f"    status: {ro.get('status')}")
    print(f"    operation_path: {ro.get('operation_path')}")
    print(f"    relation_bound: {ro.get('relation_bound')}")
    print(f"    parameter_bindings: {ro.get('parameter_bindings')}")
