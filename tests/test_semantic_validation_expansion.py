from __future__ import annotations

from ai_test_asset_center.experiment_protocols import compile_family_protocol
from ai_test_asset_center.validation_obligation_expander import (
    expand_validation_obligation,
)


def _documented_registration_operation() -> dict:
    return {
        "id": "op-register-account",
        "method": "POST",
        "path": "/accounts",
        "read_write": "write",
        "request_schema": {
            "content": {
                "application/json": {
                    "example": {
                        "email": "new@example.com",
                        "password": "Strong@123",
                        "name": "New User",
                    },
                    "schema": {
                        "type": "object",
                        "properties": {
                            "email": {
                                "type": "string",
                                "example": "new@example.com",
                            },
                            "password": {
                                "type": "string",
                                "example": "Strong@123",
                            },
                            "name": {
                                "type": "string",
                                "example": "New User",
                            },
                        },
                    },
                },
            },
        },
        "request_example": {
            "email": "new@example.com",
            "password": "Strong@123",
            "name": "New User",
        },
    }


def _validation_obligation() -> dict:
    return {
        "obligation_id": "obl-register-validation",
        "risk_family": "validation",
        "property": {"operation_ref": "op-register-account"},
        "required_operations": ["op-register-account"],
        "required_actors": ["actor-public"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": True},
        "source_refs": [{"source_id": "api-spec"}],
    }


def test_schema_type_expansion_keeps_semantic_email_validation_reachable() -> None:
    operation = _documented_registration_operation()

    variants = expand_validation_obligation(
        _validation_obligation(),
        operation=operation,
    )

    email_semantic = next(
        variant
        for variant in variants
        if variant["property"].get("field") == "email"
        and variant["property"].get("semantic_validation_constraint")
        == "semantic:invalid_email_format"
    )
    assert "validation_constraint" not in email_semantic["property"]
    assert (
        email_semantic["property"]["semantic_validation_source"]
        == "documented_field_identity"
    )

    protocol = compile_family_protocol(
        risk_family="validation",
        operation=operation,
        operation_ref="op-register-account",
        control_actor_ref="actor-public",
        treatment_actor_ref="actor-public",
        property_spec=email_semantic["property"],
    )

    assert protocol["status"] == "COMPILED"
    assert protocol["control_plan"][0]["body"]["email"] == "new@example.com"
    assert protocol["treatment_plan"][0]["body"]["email"] == "not-an-email"
    assert (
        protocol["treatment_plan"][0]["mutation"]["constraint"]
        == "semantic:invalid_email_format"
    )
    assert protocol["assertion"]["kind"] == "validation_rejection"

    assert any(
        variant["property"].get("field") == "email"
        and variant["property"].get("validation_constraint") == "type:string"
        for variant in variants
    )


def test_source_declared_email_pattern_remains_the_single_boundary_authority() -> None:
    operation = _documented_registration_operation()
    operation["request_schema"]["content"]["application/json"]["schema"][
        "properties"
    ]["email"]["pattern"] = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

    variants = expand_validation_obligation(
        _validation_obligation(),
        operation=operation,
    )

    email_variants = [
        variant
        for variant in variants
        if variant["property"].get("field") == "email"
    ]
    assert not any(
        variant["property"].get("semantic_validation_constraint")
        == "semantic:invalid_email_format"
        for variant in email_variants
    )
    assert any(
        variant["property"].get("validation_constraint") == "pattern"
        for variant in email_variants
    )


def test_semantic_variants_precede_generic_type_variants_without_replacing_them() -> None:
    variants = expand_validation_obligation(
        _validation_obligation(),
        operation=_documented_registration_operation(),
    )

    semantic_index = next(
        index
        for index, variant in enumerate(variants)
        if variant["property"].get("semantic_validation_constraint")
        == "semantic:invalid_email_format"
    )
    type_index = next(
        index
        for index, variant in enumerate(variants)
        if variant["property"].get("field") == "email"
        and variant["property"].get("validation_constraint") == "type:string"
    )
    assert semantic_index < type_index
