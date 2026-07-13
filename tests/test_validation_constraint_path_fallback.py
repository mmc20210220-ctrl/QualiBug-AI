from __future__ import annotations

from ai_test_asset_center.experiment_protocols import compile_family_protocol


def test_validation_protocol_builds_ascii_json_path_when_metadata_is_missing() -> None:
    operation = {
        "id": "op-create",
        "method": "POST",
        "path": "/resources",
        "request_schema": {
            "type": "object",
            "required": ["lineItems"],
            "properties": {
                "lineItems": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["unit-price"],
                        "properties": {
                            "unit-price": {
                                "type": "number",
                                "minimum": 0,
                            }
                        },
                    },
                }
            },
        },
        "request_example": {
            "lineItems": [{"unit-price": 10.0}],
        },
    }

    protocol = compile_family_protocol(
        risk_family="validation",
        operation=operation,
        operation_ref="op-create",
        control_actor_ref="actor-public",
        treatment_actor_ref="actor-public",
        property_spec={
            "field_tokens": ["lineItems", 0, "unit-price"],
            "validation_constraint": "minimum",
            "validation_constraint_value": 0,
        },
    )

    assert protocol["status"] == "COMPILED"
    mutation = protocol["treatment_plan"][0]["mutation"]
    assert mutation["json_path"] == "$.lineItems[0]['unit-price']"
    assert all(ord(char) < 128 for char in mutation["json_path"])
    assert protocol["treatment_plan"][0]["body"]["lineItems"][0]["unit-price"] < 0
