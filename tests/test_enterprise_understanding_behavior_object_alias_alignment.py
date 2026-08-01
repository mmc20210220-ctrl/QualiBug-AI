from __future__ import annotations

from copy import deepcopy

from benchmark_evaluator.enterprise_understanding.alignment import (
    align_enterprise_understanding,
)


def test_source_object_alias_reaches_governed_behavior_alignment_without_writeback() -> None:
    asset = {
        "enterprise_understanding_model": {
            "business_objects": [
                {
                    "object_id": "enterprise_entity:ticket",
                    "name": "Ticket",
                    "aliases": ["工单"],
                    "status": "CONFIRMED",
                }
            ],
            "actors": [
                {
                    "actor_id": "actor:customer",
                    "name": "CUSTOMER",
                    "aliases": ["客户"],
                    "status": "CONFIRMED",
                }
            ],
            "operations": [],
            "object_relations": [],
            "state_transitions": [],
            "rules": [],
            "business_behaviors": [
                {
                    "behavior_id": "behavior:list-own-tickets",
                    "actor_refs": ["actor:customer"],
                    "operation_ref": "查看",
                    "object_refs": ["enterprise_entity:ticket"],
                    "preconditions": [],
                    "permission_decision": "ALLOW",
                    "status": "CONFIRMED",
                }
            ],
            "behavior_implementation_bindings": [
                {
                    "binding_id": "binding:list-own-tickets",
                    "behavior_ref": "behavior:list-own-tickets",
                    "api_operation_bindings": [
                        {
                            "interface_id": "api:GET:/tickets",
                            "operation_id": "listTickets",
                            "status": "BOUND",
                            "authoritative": True,
                        }
                    ],
                }
            ],
            "conflicts": [],
            "unknowns": [],
        }
    }
    ground_truth = {
        "business_objects": [],
        "actors": [],
        "operations": [],
        "object_relations": [],
        "state_transitions": [],
        "business_rules": [],
        "business_behaviors": [
            {
                "ground_truth_id": "gt:behavior:list-own-tickets",
                "actor_refs": ["CUSTOMER"],
                "operation": "listTickets",
                "object_refs": ["Ticket"],
                "preconditions": [],
                "permission_decision": "ALLOW",
                "criticality": "P0",
            }
        ],
        "conflicts": [],
        "expected_unknowns": [],
    }
    before = deepcopy(asset)

    alignment = align_enterprise_understanding(ground_truth, asset)
    row = next(
        item
        for item in alignment["alignments"]
        if item["ground_truth_id"] == "gt:behavior:list-own-tickets"
    )

    assert row["alignment_status"] == "EXACT_MATCH"
    assert row["candidate_id"] == "behavior:list-own-tickets"
    assert asset == before
