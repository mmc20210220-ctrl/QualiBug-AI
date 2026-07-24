"""Integration test for STATE_PATH_NOT_EXPLORED fix."""
from ai_test_asset_center.deep_experiment_planner import plan_deep_experiments

# Project D style obligations for STATE_PATH_NOT_EXPLORED targets
obligations = [
    # TSLA-BUG-012: Escalate from RESOLVED/CLOSED
    {
        "obligation_id": "obl_state_012",
        "risk_family": "state_transition",
        "property": {
            "invariant_ref": "inv_state_012",
            "operation_ref": "escalate_ticket",
            "expression": {
                "rule_type": "STATE_TRANSITION",
                "description": "Escalate only from OPEN/ASSIGNED/IN_PROGRESS",
                "allowed_states": ["OPEN", "ASSIGNED", "IN_PROGRESS"],
                "forbidden_states": ["RESOLVED", "CLOSED"],
            },
        },
        "source_refs": [{"source_id": "BR-TKT-007"}],
    },
    # TSLA-BUG-019: Reopen from OPEN/IN_PROGRESS
    {
        "obligation_id": "obl_state_019",
        "risk_family": "state_transition",
        "property": {
            "invariant_ref": "inv_state_019",
            "operation_ref": "reopen_ticket",
            "expression": {
                "rule_type": "STATE_TRANSITION",
                "description": "Reopen only from RESOLVED/CLOSED",
                "allowed_states": ["RESOLVED", "CLOSED"],
                "forbidden_states": ["OPEN", "ASSIGNED", "IN_PROGRESS"],
            },
        },
        "source_refs": [{"source_id": "BR-TKT-005"}],
    },
]

behavior_ir = {
    "actors": [
        {"id": "actor_customer", "role": "customer", "tenant": "acme", "credential_secret_ref": "secret_ref:customer", "status": "active"},
        {"id": "actor_agent", "role": "agent", "tenant": "acme", "credential_secret_ref": "secret_ref:agent", "status": "active"},
        {"id": "actor_supervisor", "role": "supervisor", "tenant": "acme", "credential_secret_ref": "secret_ref:supervisor", "status": "active"},
    ],
    "operations": [
        {"id": "create_ticket", "method": "POST", "path": "/tickets"},
        {"id": "assign_ticket", "method": "POST", "path": "/tickets/{id}/assign"},
        {"id": "start_ticket", "method": "POST", "path": "/tickets/{id}/start"},
        {"id": "resolve_ticket", "method": "POST", "path": "/tickets/{id}/resolve"},
        {"id": "close_ticket", "method": "POST", "path": "/tickets/{id}/close"},
        {"id": "escalate_ticket", "method": "POST", "path": "/tickets/{id}/escalate"},
        {"id": "reopen_ticket", "method": "POST", "path": "/tickets/{id}/reopen"},
    ],
    "relations": [
        {"relation_type": "transitions", "from_ref": "OPEN", "to_ref": "ASSIGNED", "operation_ref": "assign_ticket"},
        {"relation_type": "transitions", "from_ref": "ASSIGNED", "to_ref": "IN_PROGRESS", "operation_ref": "start_ticket"},
        {"relation_type": "transitions", "from_ref": "IN_PROGRESS", "to_ref": "RESOLVED", "operation_ref": "resolve_ticket"},
        {"relation_type": "transitions", "from_ref": "RESOLVED", "to_ref": "CLOSED", "operation_ref": "close_ticket"},
    ],
    "invariants": [
        {"id": "inv_state_012", "rule_type": "STATE_TRANSITION", "entity_ref": "Ticket"},
        {"id": "inv_state_019", "rule_type": "STATE_TRANSITION", "entity_ref": "Ticket"},
    ],
    "states": [
        {"id": "OPEN", "entity_ref": "Ticket", "initial": True},
        {"id": "ASSIGNED", "entity_ref": "Ticket"},
        {"id": "IN_PROGRESS", "entity_ref": "Ticket"},
        {"id": "RESOLVED", "entity_ref": "Ticket"},
        {"id": "CLOSED", "entity_ref": "Ticket"},
    ],
}

result = plan_deep_experiments(obligations, {}, behavior_ir, budget=50)
print(f"Planned: {result['planned_count']}")
print(f"Skipped: {result['skipped_count']}")
print(f"Mechanisms: {result['mechanism_counts']}")

# Check state path exploration results
for exp in result["deep_experiments"]:
    mech = exp.get("mechanism", "")
    rule_id = exp.get("rule_id", "")
    am = exp.get("actor_matrix_result")
    print(f"  {rule_id}: {mech} -> state_path={am}")

print("STATE_PATH_NOT_EXPLORED integration test: OK")
