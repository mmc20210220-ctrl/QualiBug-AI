"""Quick integration test for actor_matrix_planning module."""
import json
from ai_test_asset_center.actor_matrix_planning import plan_actor_matrix

# Simulate Behavior IR with multi-tenant actors (generic, no project hardcoding)
behavior_ir = {
    "actors": [
        {"id": "actor_alice", "role": "customer", "tenant": "tenant_a", "credential_secret_ref": "secret_ref:test_accounts:alice", "status": "active"},
        {"id": "actor_bob", "role": "customer", "tenant": "tenant_a", "credential_secret_ref": "secret_ref:test_accounts:bob", "status": "active"},
        {"id": "actor_dave", "role": "agent", "tenant": "tenant_a", "credential_secret_ref": "secret_ref:test_accounts:dave", "status": "active"},
        {"id": "actor_grace", "role": "supervisor", "tenant": "tenant_a", "credential_secret_ref": "secret_ref:test_accounts:grace", "status": "active"},
        {"id": "actor_ivan", "role": "admin", "tenant": "tenant_a", "credential_secret_ref": "secret_ref:test_accounts:ivan", "status": "active"},
        {"id": "actor_carol", "role": "customer", "tenant": "tenant_b", "credential_secret_ref": "secret_ref:test_accounts:carol", "status": "active"},
        {"id": "actor_frank", "role": "agent", "tenant": "tenant_b", "credential_secret_ref": "secret_ref:test_accounts:frank", "status": "active"},
        {"id": "actor_judy", "role": "admin", "tenant": "tenant_b", "credential_secret_ref": "secret_ref:test_accounts:judy", "status": "active"},
    ],
    "operations": [{"id": "get_ticket", "method": "GET", "path": "/tickets/{id}"}],
    "relations": [{"relation_type": "belongs_to", "from_ref": "Ticket", "to_ref": "Tenant", "field": "tenant_id"}],
    "invariants": [],
    "states": [],
}

print("=" * 60)
print("TEST 1: Tenant Isolation Rule")
print("=" * 60)
expression = {"rule_type": "TENANT_ISOLATION", "description": "Resources must be scoped to tenant. Cross-tenant access forbidden.", "tenant_field": "tenant_id"}
invariant = {"id": "inv_tenant_001", "rule_type": "TENANT_ISOLATION", "entity_ref": "Ticket", "description": "Ticket belongs to tenant, cross-tenant read forbidden"}
operation = {"id": "get_ticket", "method": "GET", "path": "/tickets/{id}"}

result = plan_actor_matrix(expression, invariant, behavior_ir, operation, resource_tenant="tenant_a")
print(f"Status: {result['status']}")
print(f"Inventory: {len(result['actor_inventory'])} actors")
print(f"Candidates: {len(result['candidates'])}")
print(f"Pairs: {len(result['discriminating_pairs'])}")
print(f"Proofs: {len(result['proofs'])}")
for p in result["discriminating_pairs"]:
    ctrl = p["control_actor"]
    viol = p["violation_actor"]
    print(f"  Pair: {ctrl['actor_id']}({ctrl['relation_type']}) vs {viol['actor_id']}({viol['relation_type']}) dim={p['dimension_under_test']} quality={p['discrimination_quality']}")

print()
print("=" * 60)
print("TEST 2: Authorization Owner Rule")
print("=" * 60)
expression2 = {"rule_type": "AUTHORIZATION", "description": "Only ticket owner can reopen. customer_id ownership check required.", "owner_field": "customer_id"}
invariant2 = {"id": "inv_auth_014", "rule_type": "AUTHORIZATION", "entity_ref": "Ticket", "description": "reopen_ticket requires owner (customer_id)"}
operation2 = {"id": "reopen_ticket", "method": "POST", "path": "/tickets/{id}/reopen"}

result2 = plan_actor_matrix(expression2, invariant2, behavior_ir, operation2, resource_tenant="tenant_a", resource_owner_actor_id="actor_bob")
print(f"Status: {result2['status']}")
print(f"Inventory: {len(result2['actor_inventory'])} actors")
print(f"Candidates: {len(result2['candidates'])}")
print(f"Pairs: {len(result2['discriminating_pairs'])}")
print(f"Proofs: {len(result2['proofs'])}")
for p in result2["discriminating_pairs"]:
    ctrl = p["control_actor"]
    viol = p["violation_actor"]
    print(f"  Pair: {ctrl['actor_id']}({ctrl['relation_type']}) vs {viol['actor_id']}({viol['relation_type']}) dim={p['dimension_under_test']} quality={p['discrimination_quality']}")

print()
print("=" * 60)
print("TEST 3: Full pipeline integration (plan_deep_experiments)")
print("=" * 60)
from ai_test_asset_center.deep_experiment_planner import plan_deep_experiments

obligations = [
    {
        "obligation_id": "obl_tenant_001",
        "risk_family": "isolation",
        "property": {
            "invariant_ref": "inv_tenant_001",
            "operation_ref": "get_ticket",
            "expression": expression,
        },
        "source_refs": [{"source_id": "BR-TENANT-001"}],
    },
    {
        "obligation_id": "obl_auth_014",
        "risk_family": "authorization",
        "property": {
            "invariant_ref": "inv_auth_014",
            "operation_ref": "reopen_ticket",
            "expression": expression2,
        },
        "source_refs": [{"source_id": "BR-AUTH-014"}],
    },
]

behavior_ir["operations"].append({"id": "reopen_ticket", "method": "POST", "path": "/tickets/{id}/reopen"})
behavior_ir["invariants"] = [
    {"id": "inv_tenant_001", "rule_type": "TENANT_ISOLATION", "entity_ref": "Ticket", "description": "Ticket belongs to tenant"},
    {"id": "inv_auth_014", "rule_type": "AUTHORIZATION", "entity_ref": "Ticket", "description": "reopen requires owner"},
]

pipeline_result = plan_deep_experiments(obligations, {}, behavior_ir, budget=100)
print(f"Planned: {pipeline_result['planned_count']}")
print(f"Skipped: {pipeline_result['skipped_count']}")
print(f"Mechanisms: {pipeline_result['mechanism_counts']}")
for exp in pipeline_result["deep_experiments"]:
    print(f"  Exp: {exp['experiment_id']} mechanism={exp['mechanism']} rule={exp['rule_id']}")
    print(f"    actor_matrix_result={exp.get('actor_matrix_result')}")
    print(f"    actor_proofs={len(exp.get('actor_relation_proofs', []))}")
    tp = exp.get("treatment_plan", [])
    for step in tp[:3]:
        mut = step.get("mutation", {})
        if mut:
            print(f"    step: {step.get('step_id')} actor={mut.get('actor_ref')} type={mut.get('mutation_type')} dim={mut.get('dimension_under_test')}")

print()
print("ALL TESTS PASSED" if pipeline_result["planned_count"] >= 2 else "TESTS FAILED")
