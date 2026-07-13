from __future__ import annotations

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation
from ai_test_asset_center.runtime_binding_materializer import (
    runtime_value_from_response,
)


def _state_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op-pay",
                "method": "POST",
                "path": "/orders/{id}/pay",
                "read_write": "write",
            },
            {
                "id": "op-list",
                "method": "GET",
                "path": "/orders",
                "read_write": "read",
            },
            {
                "id": "op-read",
                "method": "GET",
                "path": "/orders/{id}",
                "read_write": "read",
            },
            {
                "id": "op-cancel",
                "method": "POST",
                "path": "/orders/{id}/cancel",
                "read_write": "write",
            },
        ],
        "actors": [
            {
                "id": "actor-buyer",
                "role": "buyer",
                "account_ref": "buyer01",
                "credential_secret_ref": "secret_ref:test_accounts:buyer01",
                "runtime_bound": True,
            }
        ],
        "states": [
            {
                "id": "state-pending",
                "entity_ref": "order",
                "name": "PENDING_PAYMENT",
            },
            {
                "id": "state-paid",
                "entity_ref": "order",
                "name": "PAID",
            },
        ],
        "relations": [
            {
                "id": "permit-pay",
                "relation_type": "permits",
                "from_ref": "actor-buyer",
                "to_ref": "op-pay",
                "operation_ref": "op-pay",
                "actor_ref": "actor-buyer",
                "status": "accepted",
            }
        ],
        "conflicts": [],
    }


def _state_obligation() -> dict:
    return {
        "obligation_id": "obl-state-pay",
        "risk_family": "state",
        "property": {
            "template": "state_transition",
            "entity_ref": "order",
            "from_state_ref": "state-pending",
            "to_state_ref": "state-paid",
            "operation_ref": "op-pay",
        },
        "required_operations": ["op-pay"],
        "required_actors": [],
        "required_fixtures": ["entity_in_state:state-pending"],
        "required_observers": ["before_state", "after_state"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-cancel",
            "mode": "reverse_order",
        },
        "source_refs": [{"source_id": "state-machine"}],
    }


def test_state_transition_obligation_becomes_executable_experiment() -> None:
    experiment = compile_experiment_for_obligation(
        _state_obligation(),
        behavior_ir=_state_ir(),
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["control_plan"] == []
    assert experiment["treatment_plan"][0]["actor_ref"] == "actor-buyer"
    assert experiment["treatment_plan"][0]["protocol_step"] == "state_transition_write"

    assertion = experiment["assertions"][0]
    assert assertion["kind"] == "state_transition"
    assert assertion["from_state"] == "PENDING_PAYMENT"
    assert assertion["to_state"] == "PAID"

    assert not [
        row
        for row in experiment["binding_plan"]
        if str(row.get("fixture_id") or "").startswith("entity_in_state:")
    ]
    state_binding = next(
        row
        for row in experiment["binding_plan"]
        if row.get("target") == "id"
    )
    assert state_binding["selection_semantics"] == "source_state_precondition"
    assert state_binding["required_state"] == "PENDING_PAYMENT"
    assert state_binding["target_path"].startswith("@state=pendingpayment@")

    observer_ids = {row["observer_id"] for row in experiment["observers"]}
    assert {"before_state", "after_state"} <= observer_ids


def test_state_transition_fails_closed_without_permitted_runtime_actor() -> None:
    behavior_ir = _state_ir()
    behavior_ir["relations"] = []

    experiment = compile_experiment_for_obligation(
        _state_obligation(),
        behavior_ir=behavior_ir,
        environment_type="test",
    )

    assert experiment["compile_receipt"]["status"] == "BLOCKED"
    assert experiment["compile_receipt"]["reason_code"] == "BLOCKED_MISSING_ACTOR"
    assert (
        experiment["compile_receipt"]["detail"]
        == "state_transition_actor_unresolved"
    )


def test_state_transition_verdict_requires_the_documented_source_state() -> None:
    assertion = {
        "assertion_id": "assert-state",
        "kind": "state_transition",
        "from_state": "PENDING_PAYMENT",
        "to_state": "PAID",
    }

    passed = evaluate_assertion(
        assertion,
        observations={
            "before_state": "pending-payment",
            "after_state": "paid",
        },
    )
    assert passed["status"] == "PASS"

    wrong_precondition = evaluate_assertion(
        assertion,
        observations={
            "before_state": "CANCELLED",
            "after_state": "PAID",
        },
    )
    assert wrong_precondition["status"] == "INDETERMINATE"
    assert wrong_precondition["reason_code"] == "STATE_PRECONDITION_NOT_MET"

    violated = evaluate_assertion(
        assertion,
        observations={
            "before_state": "PENDING_PAYMENT",
            "after_state": "FAILED",
        },
    )
    assert violated["status"] == "VIOLATION"


def test_state_binding_selects_entity_in_documented_source_state() -> None:
    body = {
        "data": [
            {"id": "order-2", "status": "PAID"},
            {"id": "order-1", "status": "PENDING_PAYMENT"},
            {"id": "order-3", "status": "pending-payment"},
        ]
    }

    assert runtime_value_from_response(
        body,
        "id",
        "@state=pendingpayment@/orders/{id}/pay",
    ) == "order-1"
    assert runtime_value_from_response(
        body,
        "id",
        "@state=cancelled@/orders/{id}/pay",
    ) is None


def test_non_state_runtime_binding_behavior_remains_unchanged() -> None:
    assert runtime_value_from_response(
        {"id": "order-9"},
        "id",
        "/orders/{id}",
    ) == "order-9"
