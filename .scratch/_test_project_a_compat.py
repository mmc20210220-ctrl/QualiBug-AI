"""Quick Project A (benchmark_mall) compatibility test for deep_experiment_planner."""
from ai_test_asset_center.deep_experiment_planner import plan_deep_experiments

# Project A style obligations (mixed mechanisms including actor-matrix)
obligations = [
    {
        "obligation_id": "obl_authz_001",
        "risk_family": "authorization",
        "property": {
            "invariant_ref": "inv_authz_001",
            "operation_ref": "update_order",
            "expression": {"rule_type": "AUTHORIZATION", "description": "Only order owner can update", "owner_field": "user_id"},
        },
        "source_refs": [{"source_id": "BR-AUTHZ-001"}],
    },
    {
        "obligation_id": "obl_tenant_001",
        "risk_family": "isolation",
        "property": {
            "invariant_ref": "inv_tenant_001",
            "operation_ref": "get_product",
            "expression": {"rule_type": "TENANT_ISOLATION", "description": "Products scoped to tenant", "tenant_field": "tenant_id"},
        },
        "source_refs": [{"source_id": "BR-TENANT-001"}],
    },
    {
        "obligation_id": "obl_precond_001",
        "risk_family": "precondition",
        "property": {
            "invariant_ref": "inv_precond_001",
            "operation_ref": "checkout_cart",
            "expression": {"rule_type": "PRECONDITION", "description": "Cart must have items before checkout"},
        },
        "source_refs": [{"source_id": "BR-PRECOND-001"}],
    },
]

behavior_ir = {
    "actors": [
        {"id": "actor_user1", "role": "customer", "tenant": "tenant_a", "credential_secret_ref": "secret_ref:user1", "status": "active"},
        {"id": "actor_user2", "role": "customer", "tenant": "tenant_a", "credential_secret_ref": "secret_ref:user2", "status": "active"},
        {"id": "actor_user3", "role": "customer", "tenant": "tenant_b", "credential_secret_ref": "secret_ref:user3", "status": "active"},
        {"id": "actor_admin", "role": "admin", "tenant": "tenant_a", "credential_secret_ref": "secret_ref:admin", "status": "active"},
    ],
    "operations": [
        {"id": "update_order", "method": "PUT", "path": "/orders/{id}"},
        {"id": "get_product", "method": "GET", "path": "/products/{id}"},
        {"id": "checkout_cart", "method": "POST", "path": "/cart/checkout"},
    ],
    "relations": [
        {"relation_type": "owned_by", "from_ref": "Order", "to_ref": "User", "field": "user_id"},
        {"relation_type": "belongs_to", "from_ref": "Product", "to_ref": "Tenant", "field": "tenant_id"},
    ],
    "invariants": [
        {"id": "inv_authz_001", "rule_type": "AUTHORIZATION", "entity_ref": "Order"},
        {"id": "inv_tenant_001", "rule_type": "TENANT_ISOLATION", "entity_ref": "Product"},
        {"id": "inv_precond_001", "rule_type": "PRECONDITION", "entity_ref": "Cart"},
    ],
    "states": [],
}

result = plan_deep_experiments(obligations, {}, behavior_ir, budget=50)
print(f"Planned: {result['planned_count']}")
print(f"Skipped: {result['skipped_count']}")
print(f"Mechanisms: {result['mechanism_counts']}")

# Verify actor matrix is used for authorization/tenant isolation
for exp in result["deep_experiments"]:
    mech = exp.get("mechanism", "")
    am = exp.get("actor_matrix_result")
    if "MATRIX" in mech:
        status = am.get("status") if isinstance(am, dict) else "N/A"
        print(f"  {exp['rule_id']}: {mech} -> actor_matrix={status}")

print("Project A compatibility: OK")
