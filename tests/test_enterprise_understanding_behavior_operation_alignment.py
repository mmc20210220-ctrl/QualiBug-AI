from __future__ import annotations

from copy import deepcopy

from benchmark_evaluator.enterprise_understanding.alignment import (
    align_enterprise_understanding,
)


def _ground_truth() -> dict:
    return {
        "business_objects": [],
        "actors": [],
        "operations": [],
        "object_relations": [],
        "state_transitions": [],
        "business_rules": [],
        "business_behaviors": [
            {
                "ground_truth_id": "gt:behavior:ship-order",
                "actor_refs": ["CUSTOMER"],
                "operation": "shipOrder",
                "object_refs": ["Order"],
                "preconditions": [
                    {"field": "Order.status", "operator": "EQUALS", "value": "PAID"}
                ],
                "permission_decision": "ALLOW",
                "criticality": "P0",
            }
        ],
        "conflicts": [],
        "expected_unknowns": [],
    }


def _asset(*, authoritative: bool, status: str) -> dict:
    return {
        "enterprise_understanding_model": {
            "business_objects": [
                {
                    "object_id": "object:order",
                    "name": "Order",
                    "status": "CONFIRMED",
                }
            ],
            "actors": [
                {
                    "actor_id": "actor:customer",
                    "name": "CUSTOMER",
                    "status": "CONFIRMED",
                }
            ],
            "operations": [],
            "object_relations": [],
            "state_transitions": [],
            "rules": [],
            "business_behaviors": [
                {
                    "behavior_id": "behavior:ship-order",
                    "actor_refs": ["actor:customer"],
                    "operation_ref": "发货",
                    "object_refs": ["object:order"],
                    "preconditions": [
                        {
                            "field_candidate": "Order.status",
                            "operator_candidate": "EQUALS",
                            "value_candidate": {"raw": "PAID"},
                        }
                    ],
                    "permission_decision": "ALLOW",
                    "status": "CONFIRMED",
                }
            ],
            "behavior_implementation_bindings": [
                {
                    "binding_id": "binding:ship-order",
                    "behavior_ref": "behavior:ship-order",
                    "api_operation_bindings": [
                        {
                            "interface_id": "api:POST:/orders/{orderId}/ship",
                            "operation_id": "shipOrder",
                            "status": status,
                            "authoritative": authoritative,
                        }
                    ],
                }
            ],
            "conflicts": [],
            "unknowns": [],
        }
    }


def _behavior_alignment(asset: dict) -> dict:
    alignment = align_enterprise_understanding(_ground_truth(), asset)
    return next(
        row
        for row in alignment["alignments"]
        if row["ground_truth_id"] == "gt:behavior:ship-order"
    )


def test_behavior_alignment_joins_authoritative_bound_operation_id() -> None:
    asset = _asset(authoritative=True, status="BOUND")
    before = deepcopy(asset)

    row = _behavior_alignment(asset)

    assert row["alignment_status"] == "EXACT_MATCH"
    assert row["candidate_id"] == "behavior:ship-order"
    assert asset == before


def test_behavior_alignment_rejects_non_authoritative_operation_candidate() -> None:
    row = _behavior_alignment(_asset(authoritative=False, status="AMBIGUOUS"))

    assert row["alignment_status"] == "MISSING"
    assert row["candidate_id"] == ""
