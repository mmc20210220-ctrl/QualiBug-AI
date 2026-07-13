from __future__ import annotations

import re

from ai_test_asset_center.experiment_compiler import compile_experiments
from ai_test_asset_center.validation_obligation_expander import (
    expand_validation_obligation,
)


def _operation() -> dict:
    return {
        "id": "op-order",
        "method": "POST",
        "path": "/orders",
        "read_write": "write",
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["customer", "items"],
                        "properties": {
                            "customer": {
                                "type": "object",
                                "required": ["address"],
                                "properties": {
                                    "address": {
                                        "type": "object",
                                        "required": ["postalCode"],
                                        "properties": {
                                            "postalCode": {
                                                "type": "string",
                                                "pattern": r"^\d{5}$",
                                            },
                                        },
                                    },
                                },
                            },
                            "items": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "required": ["sku", "quantity"],
                                    "properties": {
                                        "sku": {
                                            "type": "string",
                                            "minLength": 2,
                                        },
                                        "quantity": {
                                            "type": "integer",
                                            "minimum": 1,
                                        },
                                    },
                                },
                            },
                        },
                    },
                    "example": {
                        "customer": {
                            "address": {
                                "postalCode": "12345",
                            },
                        },
                        "items": [
                            {
                                "sku": "A1",
                                "quantity": 2,
                            },
                        ],
                    },
                }
            }
        },
    }


def _obligation() -> dict:
    return {
        "obligation_id": "obl-nested-validation",
        "risk_family": "validation",
        "property": {
            "template": "single_dimension_mutation",
            "operation_ref": "op-order",
            "actor_ref": "actor-public",
        },
        "required_operations": ["op-order"],
        "required_actors": ["actor-public"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {
            "required": True,
            "operation_ref": "op-order-delete",
            "mode": "reverse_order",
        },
        "source_refs": [{"source_id": "api-spec"}],
    }


def _behavior_ir() -> dict:
    return {
        "operations": [
            _operation(),
            {
                "id": "op-order-list",
                "method": "GET",
                "path": "/orders",
                "read_write": "read",
            },
            {
                "id": "op-order-read",
                "method": "GET",
                "path": "/orders/{id}",
                "read_write": "read",
            },
            {
                "id": "op-order-delete",
                "method": "DELETE",
                "path": "/orders/{id}",
                "read_write": "write",
            },
        ],
        "actors": [
            {
                "id": "actor-public",
                "role": "public",
                "account_status": "active",
            },
        ],
        "relations": [],
        "conflicts": [],
    }


def test_nested_object_and_array_constraints_expand() -> None:
    variants = expand_validation_obligation(
        _obligation(),
        operation=_operation(),
    )
    pairs = [
        (
            row["property"]["json_path"],
            row["property"]["validation_constraint"],
        )
        for row in variants
    ]

    assert ("$.customer.address.postalCode", "required") in pairs
    assert ("$.customer.address.postalCode", "pattern") in pairs
    assert ("$.items", "minItems") in pairs
    assert ("$.items[0].sku", "minLength") in pairs
    assert ("$.items[0].quantity", "minimum") in pairs
    assert len(pairs) == 17
    assert len(set(pairs)) == 17
    assert all(
        row["required_observers"]
        == ["http_response", "business_effect"]
        for row in variants
    )


def test_nested_mutations_preserve_sibling_fields() -> None:
    result = compile_experiments(
        [_obligation()],
        behavior_ir=_behavior_ir(),
        environment_type="test",
    )

    assert result["compiled_count"] == 17, result
    assert result["blocked_count"] == 0, result
    experiments = {
        (
            experiment["assertions"][0]["property"]["json_path"],
            experiment["assertions"][0]["property"][
                "validation_constraint"
            ],
        ): experiment
        for experiment in result["experiments"]
    }

    required_postal = experiments[
        ("$.customer.address.postalCode", "required")
    ]["treatment_plan"][0]["body"]
    assert required_postal["customer"]["address"] == {}
    assert required_postal["items"][0] == {
        "sku": "A1",
        "quantity": 2,
    }

    invalid_postal = experiments[
        ("$.customer.address.postalCode", "pattern")
    ]["treatment_plan"][0]["body"]["customer"]["address"][
        "postalCode"
    ]
    assert re.search(r"^\d{5}$", invalid_postal) is None

    short_sku = experiments[
        ("$.items[0].sku", "minLength")
    ]["treatment_plan"][0]["body"]["items"][0]["sku"]
    assert len(short_sku) < 2

    low_quantity = experiments[
        ("$.items[0].quantity", "minimum")
    ]["treatment_plan"][0]["body"]["items"][0]["quantity"]
    assert low_quantity < 1


def test_unique_leaf_name_resolves_to_nested_path() -> None:
    obligation = _obligation()
    obligation["property"]["field"] = "postalCode"

    variants = expand_validation_obligation(
        obligation,
        operation=_operation(),
    )

    assert {
        row["property"]["json_path"]
        for row in variants
    } == {"$.customer.address.postalCode"}
    assert {
        row["property"]["validation_constraint"]
        for row in variants
    } == {"required", "type:string", "pattern"}


def test_ambiguous_leaf_name_fails_closed() -> None:
    operation = _operation()
    schema = operation["request_schema"]["content"][
        "application/json"
    ]["schema"]
    example = operation["request_schema"]["content"][
        "application/json"
    ]["example"]
    schema["properties"]["customer"]["properties"]["code"] = {
        "type": "string",
    }
    schema["properties"]["items"]["items"]["properties"]["code"] = {
        "type": "string",
    }
    example["customer"]["code"] = "customer-code"
    example["items"][0]["code"] = "item-code"

    obligation = _obligation()
    obligation["property"]["field"] = "code"
    variants = expand_validation_obligation(
        obligation,
        operation=operation,
    )

    assert len(variants) == 1
    assert variants[0]["property"]["field"] == "code"
    assert "validation_constraint" not in variants[0]["property"]
