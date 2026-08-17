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


def _model_with_timed_completion(
    *,
    read_bindings: list[dict] | None = None,
    include_poll_policy: bool = True,
    target_state: str = "APPROVED",
) -> dict:
    model = _model()
    model["operations"].insert(
        0,
        _operation(
            "bir_op_submit_order",
            method="POST",
            path="/orders/{id}/submit",
        ),
    )
    if read_bindings is not None:
        model["entities"][0]["fields"][0]["api_response_bindings"] = read_bindings
        for binding in read_bindings:
            operation_ref = binding["operation_id"]
            if not any(
                row["id"] == operation_ref for row in model["operations"]
            ):
                model["operations"].append(
                    _operation(
                        operation_ref,
                        method="GET",
                        path=f"/observer/{operation_ref}",
                    )
                )
    wait = {
        "wait_id": "wait:approve-deadline",
        "wait_kind": "TIMED_WAIT",
        "status": "BOUND",
        "source_backed": True,
        "source_node_id": "step:submit",
        "target_node_id": "step:approve",
        "time_window_constraints": [
            {
                "raw": "提交后1小时内",
                "anchor": "提交后",
                "duration": "1小时",
                "window_ms": 3_600_000,
                "source_backed": True,
            }
        ],
        "source_refs": [_source_ref("prd", locator="process:approval")],
    }
    if include_poll_policy:
        wait["async_policy"] = {
            "enabled": True,
            "expected_max_delay_ms": 3_600_000,
            "poll_interval_ms": 1_000,
            "max_attempts": 3_600,
            "required_stable_observations": 1,
            "terminal_condition": "source_declared_predicate",
        }
    model["process_graphs"] = [
        {
            "status": "COMPILED",
            "execution_graph_id": "graph:approval",
            "process_id": "process:approval",
            "nodes": [
                {
                    "node_id": "step:submit",
                    "operation_ref": "bir_op_submit_order",
                    "to_state": "SUBMITTED",
                },
                {
                    "node_id": "step:approve",
                    "operation_ref": "bir_op_approve_order",
                    "from_state": "SUBMITTED",
                    "to_state": target_state,
                },
            ],
            "wait_contracts": [wait],
            "source_refs": [_source_ref("prd", locator="process:approval")],
        }
    ]
    temporal = _fact_node(
        node_id="bir_inv_approve_deadline",
        typed_fields={
            "description": "提交后1小时内完成审批",
            "expression": {
                "kind": "temporal",
                "operator": "within",
                "window_ms": 3_600_000,
                "anchor": "提交后",
                "duration": "1小时",
                "raw": "提交后1小时内",
                "temporal_semantics": "action_deadline",
                "anchor_grounding_status": "UNRESOLVED",
            },
            "operation_refs": ["bir_op_approve_order"],
        },
        source_refs=[_source_ref("prd", locator="rule:approval-deadline")],
        confidence=0.9,
        derivation="explicit",
    )
    model["invariants"].append(temporal)
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


def test_state_postcondition_and_unique_readback_bind_timed_completion() -> None:
    bound, receipt = bind_source_effect_observers(
        _model_with_timed_completion()
    )

    wait = bound["process_graphs"][0]["wait_contracts"][0]
    assert wait["observer_operation_ref"] == "bir_op_get_order"
    assert wait["predicate"] == {
        "json_path": "$.status",
        "operator": "equals",
        "expected_value": "APPROVED",
    }
    assert wait["completion_binding"] == {
        "authority": "state_transition_postcondition_response_binding",
        "canonical_field_ref": "cf_order_status",
        "completion_invariant_refs": ["bir_inv_order_status"],
        "target_operation_ref": "bir_op_approve_order",
        "target_state": "APPROVED",
    }
    temporal = next(
        row for row in bound["invariants"]
        if row["id"] == "bir_inv_approve_deadline"
    )
    assert temporal["expression"]["anchor_grounding_status"] == "BOUND"
    assert temporal["expression"]["completion_grounding_status"] == "BOUND"
    assert temporal["expression"]["completion_observer"] == "bir_op_get_order"
    assert receipt["timed_wait_observer_bound_count"] == 1
    assert receipt["temporal_invariant_bound_count"] == 1


def test_missing_poll_policy_keeps_temporal_invariant_visibly_unresolved() -> None:
    bound, receipt = bind_source_effect_observers(
        _model_with_timed_completion(include_poll_policy=False)
    )

    wait = bound["process_graphs"][0]["wait_contracts"][0]
    assert wait["observer_operation_ref"] == "bir_op_get_order"
    assert wait["predicate"]["expected_value"] == "APPROVED"
    temporal = next(
        row for row in bound["invariants"]
        if row["id"] == "bir_inv_approve_deadline"
    )
    assert temporal["expression"]["anchor_grounding_status"] == "UNRESOLVED"
    assert receipt["temporal_invariant_bound_count"] == 0
    assert receipt["temporal_binding_reason_counts"] == {
        "TEMPORAL_POLL_POLICY_UNRESOLVED": 1
    }


def test_multiple_readbacks_never_choose_a_completion_observer() -> None:
    bound, receipt = bind_source_effect_observers(
        _model_with_timed_completion(
            read_bindings=[
                {"operation_id": "bir_op_get_order", "json_path": "$.status"},
                {"operation_id": "bir_op_get_order_audit", "json_path": "$.status"},
            ]
        )
    )

    wait = bound["process_graphs"][0]["wait_contracts"][0]
    assert "observer_operation_ref" not in wait
    assert "predicate" not in wait
    assert receipt["timed_wait_observer_bound_count"] == 0
    assert receipt["timed_wait_binding_reason_counts"] == {
        "TEMPORAL_COMPLETION_OBSERVER_AMBIGUOUS": 1
    }


def test_state_mismatch_never_joins_postcondition_to_process_target() -> None:
    bound, receipt = bind_source_effect_observers(
        _model_with_timed_completion(target_state="REJECTED")
    )

    wait = bound["process_graphs"][0]["wait_contracts"][0]
    assert "observer_operation_ref" not in wait
    assert receipt["timed_wait_binding_reason_counts"] == {
        "TEMPORAL_COMPLETION_POSTCONDITION_UNRESOLVED": 1
    }
