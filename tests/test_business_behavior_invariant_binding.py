from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.behavior_ir import (
    _content_addressed_id,
    _fact_node,
    _source_ref,
    empty_behavior_ir,
)
from ai_test_asset_center.business_behavior_invariant_binding import (
    bind_business_behavior_invariants,
)
from ai_test_asset_center.effect_observer_binding import (
    bind_source_effect_observers,
)


def _operation(operation_ref: str, *, method: str, path: str) -> dict:
    return _fact_node(
        node_id=operation_ref,
        typed_fields={
            "operation_id": operation_ref,
            "method": method,
            "path": path,
            "source_operation_refs": [operation_ref],
            "request_schema": {},
            "request_example": {},
            "response_schema": {},
            "parameters": [],
            "field_dictionary": [],
            "security": [],
            "summary": "",
            "description": "",
            "tags": [],
            "side_effect_class": "read" if method == "GET" else "write",
            "read_write": "read" if method == "GET" else "write",
            "entity_refs": ["orders"],
            "affected_fields": [],
            "examples": [],
        },
        source_refs=[_source_ref("api", locator=f"{method} {path}")],
        confidence=0.9,
        derivation="explicit",
    )


def _behavior_ir(*, duplicate_database_identity: bool = False) -> dict:
    model = empty_behavior_ir(project_id="business-outcome-field-binding")
    model["operations"] = [
        _operation(
            "bir_op_submit_order",
            method="POST",
            path="/orders/{id}/submit",
        ),
        _operation(
            "bir_op_approve_order",
            method="POST",
            path="/orders/{id}/approve",
        ),
        _operation(
            "bir_op_get_order",
            method="GET",
            path="/orders/{id}",
        ),
    ]
    fields = [
        {
            "field_id": "cf_order_status",
            "name": "status",
            "semantic_type": "STATE",
            "confidence": 0.9,
            "source_refs": [_source_ref("db", locator="orders.status")],
            "database_bindings": [{"table": "orders", "column": "status"}],
            "api_response_bindings": [
                {"operation_id": "bir_op_get_order", "json_path": "$.status"}
            ],
        }
    ]
    if duplicate_database_identity:
        fields.append(
            {
                "field_id": "cf_order_status_shadow",
                "name": "shadow_status",
                "semantic_type": "STATE",
                "confidence": 0.9,
                "source_refs": [_source_ref("db", locator="orders.status")],
                "database_bindings": [
                    {"table": "orders", "column": "status"}
                ],
                "api_response_bindings": [
                    {
                        "operation_id": "bir_op_get_order",
                        "json_path": "$.shadow_status",
                    }
                ],
            }
        )
    model["entities"] = [
        _fact_node(
            node_id="bir_ent_orders",
            typed_fields={
                "name": "orders",
                "kind": "resource",
                "fields": fields,
                "identity_fields": ["id"],
            },
            source_refs=[_source_ref("db", locator="orders")],
            confidence=0.9,
            derivation="explicit",
        )
    ]
    model["process_graphs"] = [
        {
            "status": "COMPILED",
            "execution_graph_id": "graph:approve-order",
            "process_id": "process:approve-order",
            "nodes": [
                {
                    "node_id": "step:submit",
                    "operation_ref": "bir_op_submit_order",
                    "to_state": "PENDING",
                },
                {
                    "node_id": "step:approve",
                    "operation_ref": "bir_op_approve_order",
                    "from_state": "PENDING",
                    "to_state": "APPROVED",
                },
            ],
            "wait_contracts": [
                {
                    "wait_id": "wait:approve",
                    "wait_kind": "TIMED_WAIT",
                    "status": "BOUND",
                    "source_backed": True,
                    "source_node_id": "step:submit",
                    "target_node_id": "step:approve",
                    "source_refs": [
                        _source_ref("prd", locator="process:approve-order")
                    ],
                }
            ],
            "source_refs": [
                _source_ref("prd", locator="process:approve-order")
            ],
        }
    ]
    model["model_id"] = _content_addressed_id(model)
    return model


def _knowledge_asset(*, include_slot_identity: bool = True) -> dict:
    outcome = {
        "outcome_id": "outcome:approve-state",
        "outcome_type": "STATE_TRANSITION",
        "target_object_refs": ["orders"],
        "field_ref": "status",
        "from_value": "PENDING",
        "to_value": "APPROVED",
        "mandatory": True,
        "status": "CONFIRMED",
    }
    if include_slot_identity:
        outcome["observer_slot_ref"] = "state_effect:0"
    outcome_observer_bindings = (
        [
            {
                "outcome_ref": "outcome:approve-state",
                "outcome_type": "STATE_TRANSITION",
                "status": "BOUND",
                "observer_slot_refs": ["state_effect:0"],
            }
        ]
        if include_slot_identity
        else []
    )
    return {
        "business_behaviors": [
            {
                "behavior_id": "behavior:approve-order",
                "status": "CONFIRMED",
                "candidate_only": False,
                "normalized_statement": "审批后订单状态变为APPROVED",
                "outcome_contracts": [outcome],
                "evidence": [
                    {
                        "source_id": "prd",
                        "source_locator": "rules.md#approve",
                        "quote": "审批后订单状态变为APPROVED",
                    }
                ],
            }
        ],
        "behavior_implementation_bindings": [
            {
                "binding_id": "binding:approve-order",
                "behavior_ref": "behavior:approve-order",
                "api_operation_bindings": [
                    {
                        "binding_id": "binding:approve-api",
                        "interface_id": "bir_op_approve_order",
                        "operation_id": "bir_op_approve_order",
                        "status": "BOUND",
                        "authoritative": True,
                    }
                ],
                "effect_observer_bindings": [
                    {
                        "slot_ref": "state_effect:0",
                        "source_field_candidate": "status",
                        "status": "BOUND",
                        "runtime_observer_available": True,
                        "object_table_identity_confirmed": True,
                        "bindings": [
                            {
                                "binding_kind": "DATABASE_FIELD",
                                "field_id": "source-field:orders.status",
                                "table": "orders",
                                "field": "status",
                                "authoritative": True,
                                "derivation": "exact_field_identity",
                            }
                        ],
                    }
                ],
                "outcome_observer_bindings": outcome_observer_bindings,
            }
        ],
    }


def test_governed_outcome_slot_closes_exact_canonical_field_identity() -> None:
    bound, receipt = bind_business_behavior_invariants(
        _behavior_ir(),
        _knowledge_asset(),
    )

    invariant = next(
        row
        for row in bound["invariants"]
        if row.get("business_behavior_ref") == "behavior:approve-order"
    )
    assert invariant["field_ids"] == ["cf_order_status"]
    assert invariant["expression"] == {
        "kind": "postcondition",
        "operator": "outcome_contract",
        "operands": [
            {
                "entity_ref": "orders",
                "field": "status",
                "field_id": "cf_order_status",
                "expected_value": "APPROVED",
                "observer_slot_ref": "state_effect:0",
                "field_binding_authority": (
                    "governed_outcome_observer_database_identity"
                ),
            }
        ],
        "raw": "审批后订单状态变为APPROVED",
    }
    assert receipt["canonical_field_bound_outcome_count"] == 1
    assert receipt["canonical_field_unresolved_outcome_refs"] == []

    observed, observer_receipt = bind_source_effect_observers(bound)
    assert observer_receipt["candidate_pair_count"] == 1
    assert any(
        row.get("relation_type") == "observes"
        and row.get("from_ref") == "bir_op_approve_order"
        and row.get("to_ref") == "bir_op_get_order"
        for row in observed["relations"]
    )
    wait = observed["process_graphs"][0]["wait_contracts"][0]
    assert wait["observer_operation_ref"] == "bir_op_get_order"
    assert wait["predicate"] == {
        "json_path": "$.status",
        "operator": "equals",
        "expected_value": "APPROVED",
    }


def test_raw_field_name_without_outcome_slot_identity_never_binds_by_name() -> None:
    bound, receipt = bind_business_behavior_invariants(
        _behavior_ir(),
        _knowledge_asset(include_slot_identity=False),
    )

    invariant = next(
        row
        for row in bound["invariants"]
        if row.get("business_behavior_ref") == "behavior:approve-order"
    )
    operand = invariant["expression"]["operands"][0]
    assert operand["field"] == "status"
    assert "field_id" not in operand
    assert "field_ids" not in invariant
    assert receipt["canonical_field_unresolved_outcome_refs"] == [
        "outcome:approve-state"
    ]
    assert any(
        row.get("reason_code")
        == "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_UNRESOLVED"
        for row in bound["coverage_gaps"]
    )


def test_ungoverned_bound_label_cannot_authorize_canonical_field() -> None:
    asset = _knowledge_asset()
    slot = asset["behavior_implementation_bindings"][0][
        "effect_observer_bindings"
    ][0]
    del slot["runtime_observer_available"]
    del slot["object_table_identity_confirmed"]

    bound, receipt = bind_business_behavior_invariants(
        _behavior_ir(),
        asset,
    )

    invariant = next(
        row
        for row in bound["invariants"]
        if row.get("business_behavior_ref") == "behavior:approve-order"
    )
    assert "field_id" not in invariant["expression"]["operands"][0]
    assert receipt["canonical_field_unresolved_outcome_refs"] == [
        "outcome:approve-state"
    ]


def test_duplicate_database_identity_stays_ambiguous_and_unbound() -> None:
    bound, receipt = bind_business_behavior_invariants(
        _behavior_ir(duplicate_database_identity=True),
        _knowledge_asset(),
    )

    invariant = next(
        row
        for row in bound["invariants"]
        if row.get("business_behavior_ref") == "behavior:approve-order"
    )
    assert "field_id" not in invariant["expression"]["operands"][0]
    assert receipt["canonical_field_ambiguous_outcome_refs"] == [
        "outcome:approve-state"
    ]
    gap = next(
        row
        for row in bound["coverage_gaps"]
        if row.get("reason_code")
        == "BUSINESS_BEHAVIOR_OUTCOME_FIELD_IDENTITY_AMBIGUOUS"
    )
    assert gap["candidate_field_refs"] == [
        "cf_order_status",
        "cf_order_status_shadow",
    ]


def test_non_field_outcome_does_not_hide_unique_state_completion() -> None:
    asset = _knowledge_asset()
    asset["business_behaviors"][0]["outcome_contracts"].append(
        {
            "outcome_id": "outcome:approve-permission",
            "outcome_type": "PERMISSION_DECISION",
            "target_object_refs": ["orders"],
            "expected_decision": "ALLOW",
            "mandatory": True,
            "status": "CONFIRMED",
        }
    )
    bound, _receipt = bind_business_behavior_invariants(
        _behavior_ir(),
        asset,
    )

    observed, observer_receipt = bind_source_effect_observers(bound)

    assert observer_receipt["timed_wait_observer_bound_count"] == 1
    assert (
        observed["process_graphs"][0]["wait_contracts"][0][
            "observer_operation_ref"
        ]
        == "bir_op_get_order"
    )


def test_binding_does_not_mutate_input_knowledge_asset() -> None:
    asset = _knowledge_asset()
    before = deepcopy(asset)

    bind_business_behavior_invariants(_behavior_ir(), asset)

    assert asset == before
