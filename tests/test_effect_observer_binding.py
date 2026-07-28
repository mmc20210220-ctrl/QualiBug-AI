from __future__ import annotations

from ai_test_asset_center.behavior_ir import (
    _content_addressed_id,
    _fact_node,
    _source_ref,
    empty_behavior_ir,
)
from ai_test_asset_center.effect_observer_binding import (
    bind_source_effect_observers,
)
from ai_test_asset_center.runtime_binding_graph import declared_effect_observers


def _operation(
    operation_ref: str,
    *,
    method: str,
    path: str,
) -> dict:
    return _fact_node(
        node_id=operation_ref,
        typed_fields={
            "operation_id": operation_ref,
            "method": method,
            "path": path,
            "source_operation_refs": [operation_ref],
            "request_schema": {"type": "object", "properties": {}},
            "request_example": {},
            "response_schema": {},
            "parameters": [],
            "field_dictionary": [],
            "security": [],
            "summary": "",
            "description": "",
            "tags": [],
            "side_effect_class": "read" if method in {"GET", "HEAD"} else "write",
            "read_write": "read" if method in {"GET", "HEAD"} else "write",
            "entity_refs": [],
            "affected_fields": [],
            "examples": [],
        },
        source_refs=[_source_ref("api_spec", locator=f"{method} {path}")],
        confidence=0.9,
        derivation="explicit",
    )


def _model(*, with_read_binding: bool = True) -> dict:
    write = _operation(
        "bir_op_approve_order",
        method="POST",
        path="/orders/{id}/approve",
    )
    read = _operation(
        "bir_op_get_order",
        method="GET",
        path="/orders/{id}",
    )
    field = {
        "field_id": "cf_order_status",
        "name": "status",
        "semantic_type": "STATE",
        "confidence": 0.9,
        "source_refs": [_source_ref("db_schema", locator="orders.status")],
        "api_response_bindings": (
            [{"operation_id": "bir_op_get_order", "json_path": "$.status"}]
            if with_read_binding
            else []
        ),
    }
    entity = _fact_node(
        node_id="bir_ent_orders",
        typed_fields={
            "name": "orders",
            "kind": "resource",
            "fields": [field],
            "identity_fields": ["id"],
        },
        source_refs=[_source_ref("db_schema", locator="orders")],
        confidence=0.9,
        derivation="explicit",
    )
    invariant = _fact_node(
        node_id="bir_inv_order_status",
        typed_fields={
            "description": "Approving an order changes its status",
            "expression": {
                "kind": "postcondition",
                "operator": "must_become",
                "operands": [{
                    "field_id": "cf_order_status",
                    "field": "status",
                    "expected_value": "APPROVED",
                }],
                "raw": "Approving an order changes its status",
            },
            "operation_refs": ["bir_op_approve_order"],
            "source_rule_refs": ["rule:approve-order"],
            "field_ids": ["cf_order_status"],
        },
        source_refs=[_source_ref("prd", locator="rule:approve-order")],
        confidence=0.85,
        derivation="explicit",
    )
    model = empty_behavior_ir(project_id="effect-observer-test")
    model["operations"] = [write, read]
    model["entities"] = [entity]
    model["invariants"] = [invariant]
    model["model_id"] = _content_addressed_id(model)
    return model


def test_canonical_response_binding_becomes_formal_effect_observer() -> None:
    bound, receipt = bind_source_effect_observers(_model())

    assert receipt["status"] == "BOUND"
    assert receipt["candidate_pair_count"] == 1
    assert receipt["added_relation_count"] == 1
    relation = next(
        row
        for row in bound["relations"]
        if row["from_ref"] == "bir_op_approve_order"
        and row["to_ref"] == "bir_op_get_order"
    )
    assert relation["relation_type"] == "observes"
    assert relation["operation_ref"] == "bir_op_get_order"
    assert relation["effects"][0]["canonical_field_refs"] == [
        "cf_order_status"
    ]

    observers = declared_effect_observers(
        bound["operations"][0],
        behavior_ir=bound,
    )
    assert [row["operation_ref"] for row in observers] == [
        "bir_op_get_order"
    ]


def test_missing_read_binding_remains_visible_and_never_invents_observer() -> None:
    bound, receipt = bind_source_effect_observers(
        _model(with_read_binding=False)
    )

    assert receipt["status"] == "BOUND_WITH_GAPS"
    assert receipt["field_without_read_binding_ids"] == [
        "cf_order_status"
    ]
    assert receipt["added_relation_count"] == 0
    assert bound["relations"] == []
