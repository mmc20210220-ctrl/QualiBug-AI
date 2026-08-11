from __future__ import annotations

import re

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
                            "name": {
                                "type": "string",
                                "minLength": 3,
                                "maxLength": 8,
                                "pattern": "^[A-Za-z]+$",
                            },
                            "externalRef": {
                                "type": "string",
                                "enum": ["ref-1", "ref-2"],
                            },
                            "quantity": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 10,
                            },
                            "note": {
                                "type": "string",
                            },
                        },
                    },
                    "example": {
                        "name": "Widget",
                        "externalRef": "ref-1",
                        "quantity": 5,
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


def _constraint_pairs(variants: list[dict]) -> list[tuple[str, str]]:
    return [
        (
            row["property"]["field"],
            row["property"]["validation_constraint"],
        )
        for row in variants
    ]


def test_documented_constraints_expand_to_independent_obligations() -> None:
    variants = expand_validation_obligation(
        _obligation(),
        operation=_operation(),
    )

    assert _constraint_pairs(variants) == [
        ("name", "required"),
        ("name", "type:string"),
        ("name", "minLength"),
        ("name", "maxLength"),
        ("name", "pattern"),
        ("externalRef", "required"),
        ("externalRef", "type:string"),
        ("externalRef", "enum"),
        ("quantity", "required"),
        ("quantity", "type:integer"),
        ("quantity", "minimum"),
        ("quantity", "maximum"),
        ("note", "type:string"),
    ]
    assert len({row["obligation_id"] for row in variants}) == 13
    assert all(
        row["required_observers"]
        == ["http_response", "business_effect"]
        for row in variants
    )


def test_compile_experiments_emits_one_mutation_per_constraint() -> None:
    obligation = _obligation()
    result = compile_experiments(
        [obligation],
        behavior_ir=_behavior_ir(),
        environment_type="test",
    )

    assert result["compiled_count"] == 13, result
    assert result["blocked_count"] == 0, result
    assert obligation["expanded_experiment_count"] == 13
    assert obligation["compiled_experiment_count"] == 13

    experiments = {
        (
            experiment["assertions"][0]["property"]["field"],
            experiment["assertions"][0]["property"][
                "validation_constraint"
            ],
        ): experiment
        for experiment in result["experiments"]
    }
    assert len(experiments) == 13

    for experiment in experiments.values():
        observer_ids = {
            row["observer_id"]
            for row in experiment["observers"]
        }
        assert {"http_response", "business_effect"} <= observer_ids
        assertion = experiment["assertions"][0]
        assert assertion["kind"] == "validation_rejection"
        assert assertion["expected_class"] == 4
        assert assertion["expected_effect_count"] == 0
        assert assertion["expected_control_effect_min"] == 1

    assert "name" not in experiments[
        ("name", "required")
    ]["treatment_plan"][0]["body"]
    assert experiments[
        ("name", "type:string")
    ]["treatment_plan"][0]["body"]["name"] == {}

    min_length_value = experiments[
        ("name", "minLength")
    ]["treatment_plan"][0]["body"]["name"]
    assert isinstance(min_length_value, str)
    assert len(min_length_value) < 3

    max_length_value = experiments[
        ("name", "maxLength")
    ]["treatment_plan"][0]["body"]["name"]
    assert isinstance(max_length_value, str)
    assert len(max_length_value) > 8

    pattern_value = experiments[
        ("name", "pattern")
    ]["treatment_plan"][0]["body"]["name"]
    assert isinstance(pattern_value, str)
    assert re.search(r"^[A-Za-z]+$", pattern_value) is None
    assert 3 <= len(pattern_value) <= 8

    enum_value = experiments[
        ("externalRef", "enum")
    ]["treatment_plan"][0]["body"]["externalRef"]
    assert enum_value not in {"ref-1", "ref-2"}

    minimum_value = experiments[
        ("quantity", "minimum")
    ]["treatment_plan"][0]["body"]["quantity"]
    assert isinstance(minimum_value, int)
    assert minimum_value < 1

    maximum_value = experiments[
        ("quantity", "maximum")
    ]["treatment_plan"][0]["body"]["quantity"]
    assert isinstance(maximum_value, int)
    assert maximum_value > 10


def test_explicit_field_expands_only_its_documented_constraints() -> None:
    obligation = _obligation()
    obligation["property"]["field"] = "name"

    variants = expand_validation_obligation(
        obligation,
        operation=_operation(),
    )

    assert _constraint_pairs(variants) == [
        ("name", "required"),
        ("name", "type:string"),
        ("name", "minLength"),
        ("name", "maxLength"),
        ("name", "pattern"),
    ]


def test_explicit_constraint_is_not_expanded_again() -> None:
    obligation = _obligation()
    obligation["property"].update({
        "field": "name",
        "validation_constraint": "pattern",
        "validation_constraint_value": "^[A-Za-z]+$",
    })

    variants = expand_validation_obligation(
        obligation,
        operation=_operation(),
    )

    assert len(variants) == 1
    assert variants[0]["property"]["validation_constraint"] == "pattern"
    assert variants[0]["required_observers"] == [
        "http_response",
        "business_effect",
    ]


def test_semantic_invariant_is_not_cross_producted_with_request_schema() -> None:
    """A rule obligation and a schema-coverage obligation are different facts.

    An invariant already carries the property it is meant to verify.  Expanding
    it once per unrelated request field silently replaces that property with a
    generic type/required check and can also make the protocol shape invalid.
    """

    obligation = _obligation()
    obligation["property"] = {
        "template": "invariant_validation",
        "invariant_ref": "rule-visible-state",
        "operation_ref": "op-create",
        "actor_ref": "actor-public",
        "expression": {
            "kind": "business_rule",
            "operator": "must_hold",
            "operands": [],
            "raw": "Only source-approved records may be visible.",
        },
    }

    variants = expand_validation_obligation(
        obligation,
        operation=_operation(),
    )

    assert len(variants) == 1
    assert variants[0]["obligation_id"] == "obl-create-validation"
    assert variants[0]["property"] == obligation["property"]
    assert "validation_constraint" not in variants[0]["property"]


def test_typed_invariant_operand_targets_exact_source_constraint() -> None:
    obligation = _obligation()
    obligation["property"]["expression"] = {
        "kind": "business_rule",
        "operator": "field_constraint",
        "operands": [
            {
                "field_tokens": ["quantity"],
                "validation_constraint": "exclusiveMinimum",
                "validation_constraint_value": 0,
            }
        ],
    }

    variants = expand_validation_obligation(
        obligation,
        operation=_operation(),
    )

    assert len(variants) == 1
    assert variants[0]["property"]["field_tokens"] == ["quantity"]
    assert variants[0]["property"]["validation_constraint"] == "exclusiveMinimum"
    assert variants[0]["property"]["validation_constraint_value"] == 0
    assert (
        variants[0]["property"]["validation_constraint_source"]
        == "source_invariant"
    )


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

    session_scope = evaluate_assertion(
        {
            **assertion,
            "business_effect_requirement": "NOT_APPLICABLE",
        },
        observations={
            "status_code": 422,
            "business_effect_not_applicable": True,
            "business_effect_not_applicable_basis": "source_path_semantics",
        },
    )
    assert session_scope["status"] == "PASS"
    assert session_scope["actual"]["business_effect_status"] == "NOT_APPLICABLE"
    assert session_scope["actual"]["treatment_effect_count"] is None


def test_exclusive_numeric_and_array_boundaries_are_mutated() -> None:
    from ai_test_asset_center.experiment_protocols import (
        compile_family_protocol,
    )

    operation = {
        "id": "op-boundaries",
        "method": "POST",
        "path": "/boundaries",
        "read_write": "write",
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "score": {
                                "type": "integer",
                                "exclusiveMinimum": 0,
                                "exclusiveMaximum": 5,
                            },
                            "tags": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 2,
                            },
                        },
                    },
                    "example": {
                        "score": 3,
                        "tags": ["source"],
                    },
                }
            }
        },
    }

    variants = expand_validation_obligation(
        {
            **_obligation(),
            "property": {
                "operation_ref": "op-boundaries",
                "actor_ref": "actor-public",
            },
            "required_operations": ["op-boundaries"],
        },
        operation=operation,
    )
    assert _constraint_pairs(variants) == [
        ("score", "type:integer"),
        ("score", "exclusiveMinimum"),
        ("score", "exclusiveMaximum"),
        ("tags", "type:array"),
        ("tags", "minItems"),
        ("tags", "maxItems"),
    ]

    treatments = {}
    for variant in variants:
        prop = variant["property"]
        protocol = compile_family_protocol(
            risk_family="validation",
            operation=operation,
            operation_ref="op-boundaries",
            control_actor_ref="actor-public",
            treatment_actor_ref="actor-public",
            property_spec=prop,
        )
        assert protocol["status"] == "COMPILED", protocol
        treatments[
            (prop["field"], prop["validation_constraint"])
        ] = protocol["treatment_plan"][0]["body"]

    assert treatments[
        ("score", "exclusiveMinimum")
    ]["score"] == 0
    assert treatments[
        ("score", "exclusiveMaximum")
    ]["score"] == 5
    assert treatments[
        ("tags", "minItems")
    ]["tags"] == []
    assert len(
        treatments[("tags", "maxItems")]["tags"]
    ) == 3
