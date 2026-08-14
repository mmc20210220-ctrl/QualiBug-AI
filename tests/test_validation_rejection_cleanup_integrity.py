from __future__ import annotations

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.experiment_protocols import compile_family_protocol
from ai_test_asset_center.runtime_binding_materializer import (
    runtime_cleanup_paths,
)


def _validation_assertion() -> dict:
    return {
        "assertion_id": "assert-validation",
        "kind": "validation_rejection",
        "expected_class": 4,
        "expected_effect_count": 0,
        "expected_control_effect_min": 1,
    }


def test_validation_pass_requires_a_real_positive_control_effect() -> None:
    passed = evaluate_assertion(
        _validation_assertion(),
        observations={
            "status_code": 422,
            "business_effect_observed": True,
            "control_effect_count": 1,
            "treatment_effect_count": 0,
        },
    )
    assert passed["status"] == "PASS"
    assert passed["actual"]["control_effect_count"] == 1

    inert_control = evaluate_assertion(
        _validation_assertion(),
        observations={
            "status_code": 422,
            "business_effect_observed": True,
            "control_effect_count": 0,
            "treatment_effect_count": 0,
        },
    )
    assert inert_control["status"] == "INDETERMINATE"
    assert (
        inert_control["reason_code"]
        == "VALIDATION_CONTROL_EFFECT_MISSING"
    )


def test_strong_validation_violations_are_not_masked_by_weak_control() -> None:
    dirty_rejection = evaluate_assertion(
        _validation_assertion(),
        observations={
            "status_code": 422,
            "business_effect_observed": True,
            "control_effect_count": 0,
            "treatment_effect_count": 1,
        },
    )
    assert dirty_rejection["status"] == "VIOLATION"
    assert (
        dirty_rejection["reason_code"]
        == "VALIDATION_REJECTION_SIDE_EFFECT"
    )


def test_validation_protocol_declares_control_effect_requirement() -> None:
    protocol = compile_family_protocol(
        risk_family="validation",
        operation={
            "id": "op-create",
            "method": "POST",
            "path": "/resources",
            "request_example": {"name": "valid"},
            "request_schema": {
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        },
        operation_ref="op-create",
        control_actor_ref="actor-public",
        treatment_actor_ref="actor-public",
        property_spec={
            "field": "name",
            "validation_constraint": "required",
            "validation_constraint_source": "request_schema",
        },
    )

    assert protocol["status"] == "COMPILED"
    assert protocol["assertion"]["kind"] == "validation_rejection"
    assert protocol["assertion"]["expected_control_effect_min"] == 1


def test_validation_protocol_blocks_without_source_request_material() -> None:
    protocol = compile_family_protocol(
        risk_family="validation",
        operation={
            "id": "op-update",
            "method": "PATCH",
            "path": "/resources/{id}",
        },
        operation_ref="op-update",
        control_actor_ref="actor-owner",
        treatment_actor_ref="actor-owner",
        property_spec={"field": "status"},
    )

    assert protocol["status"] == "BLOCKED"
    assert protocol["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert protocol["detail"] == "validation_requires_source_example_and_request_schema"


def test_rejected_create_with_snapshot_proven_side_effect_is_cleaned() -> None:
    paths, missing = runtime_cleanup_paths(
        "/resources/{id}",
        [{
            "phase": "treatment",
            "step_id": "treatment_1",
            "method": "POST",
            "path": "/resources",
            "status_code": 422,
            "body": {"error": "invalid"},
            "governance_receipt": {
                "accepted": False,
                "before": {"status": 200, "body": {"data": []}},
                "after": {
                    "status": 200,
                    "body": {
                        "data": [
                            {"id": "dirty-1", "name": "invalid"}
                        ]
                    },
                },
            },
        }],
    )

    assert missing == []
    assert paths == [("/resources/dirty-1", {"id": "dirty-1"})]


def test_rejected_update_uses_the_real_concrete_request_identity() -> None:
    paths, missing = runtime_cleanup_paths(
        "/resources/{id}",
        [{
            "phase": "treatment",
            "step_id": "treatment_1",
            "method": "PATCH",
            "path": "/resources/res-7",
            "status_code": 400,
            "body": {"message": "invalid quantity"},
            "governance_receipt": {
                "accepted": False,
                "before": {
                    "status": 200,
                    "body": {"id": "res-7", "quantity": 1},
                },
                "after": {
                    "status": 200,
                    "body": {"id": "res-7", "quantity": -1},
                },
            },
        }],
    )

    assert missing == []
    assert paths == [("/resources/res-7", {"id": "res-7"})]


def test_rejected_write_without_side_effect_does_not_trigger_cleanup() -> None:
    paths, missing = runtime_cleanup_paths(
        "/resources/{id}",
        [{
            "phase": "treatment",
            "step_id": "treatment_1",
            "method": "POST",
            "path": "/resources",
            "status_code": 422,
            "body": {"error": "invalid"},
            "governance_receipt": {
                "accepted": False,
                "before": {"status": 200, "body": {"data": []}},
                "after": {"status": 200, "body": {"data": []}},
            },
        }],
    )

    assert paths == []
    assert missing == ["effectful_write_receipt"]
