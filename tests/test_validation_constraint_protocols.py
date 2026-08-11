from __future__ import annotations

import re

from ai_test_asset_center.experiment_protocols import compile_family_protocol
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
            "type": "object",
            "required": ["status", "quantity", "profile", "tags"],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["draft", "active"],
                },
                "quantity": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 5,
                },
                "profile": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {
                            "type": "string",
                            "minLength": 3,
                            "maxLength": 8,
                            "pattern": "^[a-z]+$",
                        }
                    },
                },
                "tags": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "items": {"type": "string"},
                },
            },
        },
        "request_example": {
            "status": "draft",
            "quantity": 3,
            "profile": {"name": "alice"},
            "tags": ["one"],
        },
    }


def _property(
    tokens: list[str | int],
    constraint: str,
    value: object,
) -> dict:
    path = "$"
    for token in tokens:
        path += f"[{token}]" if isinstance(token, int) else f".{token}"
    return {
        "field": str(tokens[-1]),
        "field_tokens": tokens,
        "field_path": ".".join(str(token) for token in tokens),
        "json_path": path,
        "validation_constraint": constraint,
        "validation_constraint_value": value,
    }


def _compile(property_spec: dict) -> dict:
    return compile_family_protocol(
        risk_family="validation",
        operation=_operation(),
        operation_ref="op-create",
        control_actor_ref="actor-public",
        treatment_actor_ref="actor-public",
        property_spec=property_spec,
    )


def test_enum_and_numeric_boundaries_compile_to_exact_mutations() -> None:
    enum_protocol = _compile(
        _property(["status"], "enum", ["draft", "active"])
    )
    assert enum_protocol["status"] == "COMPILED"
    enum_step = enum_protocol["treatment_plan"][0]
    assert enum_step["mutation"]["constraint"] == "enum"
    assert enum_step["body"]["status"] not in {"draft", "active"}
    assert isinstance(enum_step["body"]["status"], str)

    minimum_protocol = _compile(
        _property(["quantity"], "minimum", 1)
    )
    assert minimum_protocol["status"] == "COMPILED"
    assert minimum_protocol["treatment_plan"][0]["body"]["quantity"] < 1
    assert (
        minimum_protocol["treatment_plan"][0]["mutation"]["constraint"]
        == "minimum"
    )

    maximum_protocol = _compile(
        _property(["quantity"], "maximum", 5)
    )
    assert maximum_protocol["status"] == "COMPILED"
    assert maximum_protocol["treatment_plan"][0]["body"]["quantity"] > 5


def test_mutation_receipt_preserves_constraint_source_lineage() -> None:
    property_spec = _property(["quantity"], "exclusiveMinimum", 0)
    property_spec["validation_constraint_source"] = "source_invariant"

    protocol = _compile(property_spec)

    assert protocol["status"] == "COMPILED"
    assert (
        protocol["treatment_plan"][0]["mutation"]["source"]
        == "source_invariant"
    )


def test_nested_required_and_string_constraints_mutate_the_nested_leaf() -> None:
    required_protocol = _compile(
        _property(["profile", "name"], "required", True)
    )
    assert required_protocol["status"] == "COMPILED"
    required_body = required_protocol["treatment_plan"][0]["body"]
    assert "profile" in required_body
    assert "name" not in required_body["profile"]
    assert required_body["status"] == "draft"

    min_length_protocol = _compile(
        _property(["profile", "name"], "minLength", 3)
    )
    min_value = min_length_protocol["treatment_plan"][0]["body"]["profile"]["name"]
    assert len(min_value) < 3
    assert min_length_protocol["treatment_plan"][0]["body"]["status"] == "draft"

    max_length_protocol = _compile(
        _property(["profile", "name"], "maxLength", 8)
    )
    max_value = max_length_protocol["treatment_plan"][0]["body"]["profile"]["name"]
    assert len(max_value) > 8

    pattern_protocol = _compile(
        _property(["profile", "name"], "pattern", "^[a-z]+$")
    )
    pattern_value = pattern_protocol["treatment_plan"][0]["body"]["profile"]["name"]
    assert re.search("^[a-z]+$", pattern_value) is None
    assert (
        pattern_protocol["treatment_plan"][0]["mutation"]["json_path"]
        == "$.profile.name"
    )


def test_array_boundaries_compile_to_exact_length_violations() -> None:
    min_items_protocol = _compile(
        _property(["tags"], "minItems", 1)
    )
    assert min_items_protocol["status"] == "COMPILED"
    assert len(min_items_protocol["treatment_plan"][0]["body"]["tags"]) < 1

    max_items_protocol = _compile(
        _property(["tags"], "maxItems", 2)
    )
    assert max_items_protocol["status"] == "COMPILED"
    assert len(max_items_protocol["treatment_plan"][0]["body"]["tags"]) > 2


def test_unconstructible_same_type_enum_violation_fails_closed() -> None:
    operation = {
        "id": "op-toggle",
        "method": "POST",
        "path": "/toggle",
        "request_schema": {
            "type": "object",
            "required": ["enabled"],
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "enum": [True, False],
                }
            },
        },
        "request_example": {"enabled": True},
    }
    protocol = compile_family_protocol(
        risk_family="validation",
        operation=operation,
        operation_ref="op-toggle",
        control_actor_ref="actor-public",
        treatment_actor_ref="actor-public",
        property_spec=_property(
            ["enabled"],
            "enum",
            [True, False],
        ),
    )

    assert protocol["status"] == "BLOCKED"
    assert protocol["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert "source_constraint_mutation_unavailable:enum" in protocol["detail"]


def test_expanded_constraints_survive_into_matching_protocol_mutations() -> None:
    obligation = {
        "obligation_id": "obl-validation",
        "risk_family": "validation",
        "property": {"operation_ref": "op-create"},
        "required_operations": ["op-create"],
        "required_actors": ["actor-public"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": True},
        "source_refs": [{"source_id": "api-spec"}],
    }
    variants = expand_validation_obligation(
        obligation,
        operation=_operation(),
    )
    selected = [
        variant
        for variant in variants
        if variant["property"]["validation_constraint"]
        in {"enum", "minimum", "maximum", "minLength", "maxItems"}
    ]
    assert selected

    for variant in selected:
        protocol = _compile(variant["property"])
        assert protocol["status"] == "COMPILED", variant
        mutation = protocol["treatment_plan"][0]["mutation"]
        assert (
            mutation["constraint"]
            == variant["property"]["validation_constraint"]
        )
        assert mutation["json_path"] == variant["property"]["json_path"]
        assert protocol["assertion"]["kind"] == "validation_rejection"
        assert protocol["assertion"]["expected_control_effect_min"] == 1


def test_explicit_request_constraint_supersedes_one_sided_semantic_projection() -> None:
    """A typed request constraint has its own executable two-arm protocol.

    The semantic base may project the surrounding rule as a response-only
    observation.  That one-sided plan must not make the independently sourced
    request constraint look like an adapter capability gap.
    """

    operation = {
        "id": "op-update",
        "method": "POST",
        "path": "/resources/{id}/state",
        "read_write": "write",
        "request_schema": {
            "type": "object",
            "required": ["nextState"],
            "properties": {"nextState": {"type": "string"}},
        },
        "request_example": {"nextState": "source-declared"},
        "response_schema": {
            "type": "object",
            "properties": {"status": {"type": "string"}},
        },
    }
    property_spec = {
        "template": "invariant_validation",
        "expression": {
            "kind": "validation",
            "operator": "must_hold",
            "operands": [{"field": "status"}],
            "raw": "A textual outcome must be present.",
        },
        "field": "nextState",
        "field_tokens": ["nextState"],
        "json_path": "$.nextState",
        "validation_constraint": "required",
        "validation_constraint_source": "request_schema",
    }

    protocol = compile_family_protocol(
        risk_family="validation",
        operation=operation,
        operation_ref="op-update",
        control_actor_ref="actor-public",
        treatment_actor_ref="actor-public",
        property_spec=property_spec,
    )

    assert protocol["status"] == "COMPILED"
    assert len(protocol["control_plan"]) == 1
    assert len(protocol["treatment_plan"]) == 1
    assert protocol["control_plan"][0]["body"] == {
        "nextState": "source-declared"
    }
    assert protocol["treatment_plan"][0]["body"] == {}
    assert protocol["assertion"]["kind"] == "validation_rejection"
