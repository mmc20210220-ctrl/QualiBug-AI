from __future__ import annotations

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.experiment_compiler import compile_experiments
from ai_test_asset_center.validation_obligation_expander import (
    expand_validation_obligation,
)


def _operation() -> dict:
    return {
        "id": "op-create",
        "method": "POST",
        "path": "/resources",
        "read_write": "write",
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": [
                            "name",
                            "externalRef",
                            "quantity",
                        ],
                        "properties": {
                            "name": {"type": "string"},
                            "externalRef": {"type": "string"},
                            "quantity": {"type": "integer"},
                            "note": {"type": "string"},
                        },
                    },
                    "example": {
                        "name": "source-declared",
                        "externalRef": "ref-1",
                        "quantity": 1,
                        "note": "documented",
                    },
                }
            }
        },
    }


def _obligation() -> dict:
    return {
        "obligation_id": "obl-create-validation",
        "risk_family": "validation",
        "property": {
            "template": "single_dimension_mutation",
            "operation_ref": "op-create",
            "actor_ref": "actor-public",
        },
        "required_operations": ["op-create"],
        "required_actors": ["actor-public"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-delete",
            "mode": "reverse_order",
        },
        "source_refs": [{"source_id": "api-spec"}],
    }


def _behavior_ir() -> dict:
    return {
        "operations": [
            _operation(),
            {
                "id": "op-list",
                "method": "GET",
                "path": "/resources",
                "read_write": "read",
            },
            {
                "id": "op-read",
                "method": "GET",
                "path": "/resources/{id}",
                "read_write": "read",
            },
            {
                "id": "op-delete",
                "method": "DELETE",
                "path": "/resources/{id}",
                "read_write": "write",
            },
        ],
        "actors": [
            {
                "id": "actor-public",
                "role": "public",
                "account_status": "active",
            }
        ],
        "relations": [],
        "conflicts": [],
    }


def test_required_fields_expand_to_independent_validation_obligations() -> None:
    variants = expand_validation_obligation(
        _obligation(),
        operation=_operation(),
    )

    assert [row["property"]["field"] for row in variants] == [
        "name",
        "externalRef",
        "quantity",
    ]
    assert all(
        row["property"]["validation_constraint"] == "required"
        for row in variants
    )
    assert len({row["obligation_id"] for row in variants}) == 3
    assert all(
        row["required_observers"]
        == ["http_response", "business_effect"]
        for row in variants
    )


def test_compile_experiments_preserves_one_mutation_per_required_field() -> None:
    obligation = _obligation()
    result = compile_experiments(
        [obligation],
        behavior_ir=_behavior_ir(),
        environment_type="test",
    )

    assert result["compiled_count"] == 3, result
    assert result["blocked_count"] == 0, result
    assert obligation["expanded_experiment_count"] == 3
    assert obligation["compiled_experiment_count"] == 3

    fields = [
        experiment["treatment_plan"][0]["mutation"]["json_path"]
        for experiment in result["experiments"]
    ]
    assert fields == ["$.name", "$.externalRef", "$.quantity"]

    for experiment in result["experiments"]:
        observer_ids = {
            row["observer_id"]
            for row in experiment["observers"]
        }
        assert {"http_response", "business_effect"} <= observer_ids
        assertion = experiment["assertions"][0]
        assert assertion["kind"] == "validation_rejection"
        assert assertion["expected_class"] == 4
        assert assertion["expected_effect_count"] == 0


def test_explicit_field_obligation_keeps_effect_observer() -> None:
    obligation = _obligation()
    obligation["property"]["field"] = "name"

    variants = expand_validation_obligation(
        obligation,
        operation=_operation(),
    )

    assert len(variants) == 1
    assert variants[0]["property"]["field"] == "name"
    assert variants[0]["required_observers"] == [
        "http_response",
        "business_effect",
    ]


def test_validation_rejection_requires_zero_business_effect() -> None:
    assertion = {
        "assertion_id": "assert-validation",
        "kind": "validation_rejection",
        "expected_class": 4,
        "expected_effect_count": 0,
    }

    passed = evaluate_assertion(
        assertion,
        observations={
            "status_code": 422,
            "business_effect_observed": True,
            "treatment_effect_count": 0,
        },
    )
    assert passed["status"] == "PASS"

    side_effect = evaluate_assertion(
        assertion,
        observations={
            "status_code": 422,
            "business_effect_observed": True,
            "treatment_effect_count": 1,
        },
    )
    assert side_effect["status"] == "VIOLATION"
    assert (
        side_effect["reason_code"]
        == "VALIDATION_REJECTION_SIDE_EFFECT"
    )

    accepted_invalid = evaluate_assertion(
        assertion,
        observations={
            "status_code": 200,
            "business_effect_observed": True,
            "treatment_effect_count": 0,
        },
    )
    assert accepted_invalid["status"] == "VIOLATION"
    assert (
        accepted_invalid["reason_code"]
        == "VALIDATION_REJECTION_NOT_ENFORCED"
    )

    missing_effect = evaluate_assertion(
        assertion,
        observations={
            "status_code": 422,
            "business_effect_observed": False,
        },
    )
    assert missing_effect["status"] == "INDETERMINATE"
    assert (
        missing_effect["reason_code"]
        == "VALIDATION_BUSINESS_EFFECT_MISSING"
    )
