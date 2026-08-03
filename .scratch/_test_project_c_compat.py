"""Quick Project C compatibility test for deep_experiment_planner."""
from ai_test_asset_center.deep_experiment_planner import plan_deep_experiments

# Project C style obligations (non-actor-matrix mechanisms)
obligations = [
    {
        "obligation_id": "obl_state_001",
        "risk_family": "state_transition",
        "property": {
            "invariant_ref": "inv_state_001",
            "operation_ref": "approve_contract",
            "expression": {"rule_type": "STATE_TRANSITION", "description": "Contract must be DRAFT before approval"},
        },
        "source_refs": [{"source_id": "BR-STATE-001"}],
    },
    {
        "obligation_id": "obl_validation_001",
        "risk_family": "validation",
        "property": {
            "invariant_ref": "inv_val_001",
            "operation_ref": "create_invoice",
            "expression": {"rule_type": "VALIDATION", "description": "Invoice amount must be positive"},
        },
        "source_refs": [{"source_id": "BR-VAL-001"}],
    },
]

behavior_ir = {
    "actors": [
        {"id": "actor_admin", "role": "admin", "tenant": "acme", "credential_secret_ref": "secret_ref:admin", "status": "active"},
    ],
    "operations": [
        {"id": "approve_contract", "method": "POST", "path": "/contracts/{id}/approve"},
        {"id": "create_invoice", "method": "POST", "path": "/invoices"},
    ],
    "relations": [],
    "invariants": [
        {"id": "inv_state_001", "rule_type": "STATE_TRANSITION", "entity_ref": "Contract"},
        {"id": "inv_val_001", "rule_type": "VALIDATION", "entity_ref": "Invoice"},
    ],
    "states": [],
}

result = plan_deep_experiments(obligations, {}, behavior_ir, budget=50)
print(f"Planned: {result['planned_count']}")
print(f"Skipped: {result['skipped_count']}")
print(f"Mechanisms: {result['mechanism_counts']}")
print("Project C compatibility: OK")
