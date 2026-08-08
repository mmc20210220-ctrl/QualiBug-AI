"""State-machine transition semantic binding channel (root-cause fix).

Source state machines declare legal and forbidden moves; the semantic linker
assesses each move against the documented interfaces; the binder turns the
accepted identities into IR structure. This test pins the whole channel with a
deterministic fake client — no provider, no network.
"""
from __future__ import annotations

from typing import Any

from ai_test_asset_center.agent_semantic_linker import (
    enrich_knowledge_asset_with_agent_relationships,
    transition_identity,
)
from ai_test_asset_center.semantic_operation_binding import (
    bind_accepted_semantic_operations,
)


class FakeClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.requests: list[dict] = []

    def complete_json(self, **kwargs: object) -> dict:
        self.requests.append(dict(kwargs))
        return self.response

    def usage_snapshot(self) -> dict[str, float]:
        return {"request_count": 1, "total_tokens": 100}


def _machine_asset() -> dict:
    return {
        "asset_id": "ASSET-ORDER",
        "rule_library": [{
            "rule_id": "rule:order-payment",
            "statement": "A paid order must not be cancelled directly.",
            "kind": "state_machine",
            "risk_type": "state_machine",
            "source_id": "runtime:prd:1",
            "semantic_frame": {
                "schema_version": "qualibug.business-semantic-frame.v1",
                "modality": "PROHIBITED",
                "polarity": "negative",
                "condition": "",
                "subject": "paid order",
                "behavior": "must not be cancelled directly",
                "source_anchors": [],
                "source_grounded": True,
            },
        }],
        "interfaces": [{
            "interface_id": "api:POST:/orders/{id}/cancel",
            "operation_id": "cancel_order",
            "method": "POST",
            "path": "/orders/{id}/cancel",
            "summary": "取消订单",
            "source_id": "runtime:markdown_api:2",
        }, {
            "interface_id": "api:GET:/orders",
            "operation_id": "list_orders",
            "method": "GET",
            "path": "/orders",
            "summary": "查询订单列表",
            "source_id": "runtime:markdown_api:2",
        }],
        "state_machines": [{
            "state_machine_id": "state:runtime:prd:1:1",
            "object": "order",
            "states": ["CREATED", "PENDING_PAYMENT", "PAID", "CANCELLED"],
            "transitions": [
                {"from": "CREATED", "to": "PENDING_PAYMENT"},
                {"from": "PENDING_PAYMENT", "to": "PAID"},
            ],
            "forbidden_transitions": [
                {"from": "PAID", "to": "CANCELLED"},
            ],
        }],
        "relationships": [],
    }


def _linker_response() -> dict:
    cancel = "api:POST:/orders/{id}/cancel"
    return {
        "assessments": [{
            "rule_id": "rule:order-payment",
            "disposition": "LINKED",
            "reason": "The cancel operation exercises the paid-order rule.",
            "relationships": [{
                "interface_id": cancel,
                "confidence": 0.9,
                "reason": "Cancelling is the operation the rule forbids for paid orders.",
                "evidence_refs": [
                    "rule:order-payment",
                    cancel,
                    "state:runtime:prd:1:1",
                ],
            }],
        }],
        "transition_assessments": [
            {
                "transition_id": transition_identity(
                    "state:runtime:prd:1:1", "allowed", "CREATED", "PENDING_PAYMENT"
                ),
                "disposition": "LINKED",
                "reason": "Order creation performs the transition.",
                "relationships": [{
                    "interface_id": "api:POST:/orders/{id}/cancel",
                    "confidence": 0.9,
                    "reason": "The create-order interface establishes the pending state.",
                    "evidence_refs": [
                        transition_identity(
                            "state:runtime:prd:1:1",
                            "allowed",
                            "CREATED",
                            "PENDING_PAYMENT",
                        ),
                        "api:POST:/orders/{id}/cancel",
                    ],
                }],
            },
            {
                "transition_id": transition_identity(
                    "state:runtime:prd:1:1", "allowed", "PENDING_PAYMENT", "PAID"
                ),
                "disposition": "LINKED",
                "reason": "The payment operation performs the transition.",
                "relationships": [{
                    "interface_id": cancel,
                    "confidence": 0.9,
                    "reason": "The cancel interface would be the measured window.",
                    "evidence_refs": [
                        transition_identity(
                            "state:runtime:prd:1:1",
                            "allowed",
                            "PENDING_PAYMENT",
                            "PAID",
                        ),
                        cancel,
                    ],
                }],
            },
            {
                "transition_id": transition_identity(
                    "state:runtime:prd:1:1", "forbidden", "PAID", "CANCELLED"
                ),
                "disposition": "LINKED",
                "reason": "The cancel operation is the forbidden move.",
                "relationships": [{
                    "interface_id": cancel,
                    "confidence": 0.9,
                    "reason": "Cancelling a paid order is the forbidden transition.",
                    "evidence_refs": [
                        transition_identity(
                            "state:runtime:prd:1:1",
                            "forbidden",
                            "PAID",
                            "CANCELLED",
                        ),
                        cancel,
                    ],
                }],
            },
        ],
    }


def _mini_ir() -> dict:
    """A minimal Behavior IR with the order state nodes and two operations."""
    return {
        "schema_version": "qualibug.behavior-ir.v1",
        "model_id": "mini-order",
        "entities": [
            {"id": "ent:order", "name": "order", "fields": []},
        ],
        "operations": [
            {
                "id": "bir_op_cancel",
                "operation_id": "cancel_order",
                "method": "POST",
                "path": "/orders/{id}/cancel",
                "source_refs": [],
                "confidence": 0.8,
            },
            {
                "id": "bir_op_list",
                "operation_id": "list_orders",
                "method": "GET",
                "path": "/orders",
                "source_refs": [],
                "confidence": 0.8,
            },
        ],
        "states": [
            {
                "id": "bir_state_created",
                "entity_ref": "order",
                "name": "CREATED",
                "source_refs": [],
            },
            {
                "id": "bir_state_pending",
                "entity_ref": "order",
                "name": "PENDING_PAYMENT",
                "source_refs": [],
            },
            {
                "id": "bir_state_paid",
                "entity_ref": "order",
                "name": "PAID",
                "source_refs": [],
            },
            {
                "id": "bir_state_cancelled",
                "entity_ref": "order",
                "name": "CANCELLED",
                "source_refs": [],
            },
        ],
        "invariants": [{
            "id": "bir_inv_forbidden_cancel",
            "description": "order must not transition from PAID to CANCELLED",
            "expression": {
                "kind": "forbidden_state_transition",
                "operator": "must_not_transition",
                "operands": [{
                    "entity_ref": "order",
                    "from_state": "PAID",
                    "to_state": "CANCELLED",
                }],
            },
            "operation_refs": [],
            "source_rule_refs": ["state:runtime:prd:1:1"],
            "source_refs": [],
            "confidence": 0.8,
        }],
        "relations": [],
        "coverage_gaps": [],
        "actors": [],
        "source_refs": [],
    }


def test_state_transition_edges_bind_forbidden_invariant_and_relations() -> None:
    asset = _machine_asset()
    enriched_asset, _ = enrich_knowledge_asset_with_agent_relationships(
        asset,
        client=FakeClient(_linker_response()),
    )
    transition_edges = [
        row
        for row in enriched_asset.get("relationships") or []
        if row.get("relation") == "state_transition_to_interface"
    ]
    assert len(transition_edges) == 3

    from ai_test_asset_center.behavior_ir import (
        build_behavior_ir_from_knowledge_asset,
    )

    ir = build_behavior_ir_from_knowledge_asset(
        enriched_asset,
        project_id="generic-project",
    )
    bound, _ = bind_accepted_semantic_operations(ir, enriched_asset)
    receipt = bound.get("state_transition_binding_receipt") or {}
    assert receipt.get("accepted_transition_edge_count") == 3
    assert receipt.get("bound_transition_invariant_count") == 1

    # The forbidden-transition invariant is now bound to the cancel operation.
    forbidden_invariants = [
        row
        for row in bound["invariants"]
        if (row.get("expression") or {}).get("kind") == "forbidden_state_transition"
    ]
    assert len(forbidden_invariants) == 1
    assert forbidden_invariants[0]["operation_refs"]

    # The allowed transitions became graph edges for the precondition planner.
    transition_relations = [
        row
        for row in bound["relations"]
        if row.get("relation_type") == "transitions"
    ]
    assert len(transition_relations) == 2
    assert all(row.get("operation_ref") for row in transition_relations)

    # The precondition planner can now reach PAID from the entry state.
    from ai_test_asset_center.state_precondition_planner import (
        build_transition_graph,
        plan_state_precondition,
    )

    adjacency = build_transition_graph(bound)
    assert "pending_payment" in adjacency
    planned = plan_state_precondition(
        behavior_ir=bound,
        from_state="PAID",
        actors=["buyer"],
    )
    assert planned.get("status") == "PLANNED"
    assert len(planned.get("steps") or []) == 2


def test_forbidden_transition_without_accepted_edge_stays_unbound_and_visible() -> None:
    asset = _machine_asset()
    response = _linker_response()
    # Provider declines to link the forbidden transition.
    response["transition_assessments"] = [
        row
        for row in response["transition_assessments"]
        if "forbidden" not in row["transition_id"]
    ]
    enriched_asset, _ = enrich_knowledge_asset_with_agent_relationships(
        asset,
        client=FakeClient(response),
    )
    from ai_test_asset_center.behavior_ir import (
        build_behavior_ir_from_knowledge_asset,
    )

    ir = build_behavior_ir_from_knowledge_asset(
        enriched_asset,
        project_id="generic-project",
    )
    bound, _ = bind_accepted_semantic_operations(ir, enriched_asset)
    receipt = bound.get("state_transition_binding_receipt") or {}
    assert receipt.get("bound_transition_invariant_count") == 0
    # The forbidden transition was never linked by the provider, so it is
    # neither bound nor "linked-but-unbound" — a visible unassessed gap.
    assert receipt.get("unbound_transition_ids") == []
    # Only the allowed transitions were linked; the forbidden id never appears.
    assert all(
        "forbidden" not in row
        for row in receipt.get("bound_transition_ids") or []
    )
    # The invariant stays unbound — visible gap, no heuristic fallback.
    forbidden_invariants = [
        row
        for row in bound["invariants"]
        if (row.get("expression") or {}).get("kind") == "forbidden_state_transition"
    ]
    assert forbidden_invariants
    assert all(not row.get("operation_refs") for row in forbidden_invariants)
