from __future__ import annotations

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
                        "required": ["name", "externalRef", "quantity"],
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
        row["property"]["expanded_from_obligation_id"]
        == "obl-create-validation"
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

    removed_fields = [
        field
        for experiment, field in zip(
            result["experiments"],
            ["name", "externalRef", "quantity"],
        )
        if field not in experiment["treatment_plan"][0]["body"]
    ]
    assert removed_fields == ["name", "externalRef", "quantity"]


def test_explicit_field_obligation_is_not_expanded_again() -> None:
    obligation = _obligation()
    obligation["property"]["field"] = "name"

    variants = expand_validation_obligation(
        obligation,
        operation=_operation(),
    )

    assert variants == [obligation]
