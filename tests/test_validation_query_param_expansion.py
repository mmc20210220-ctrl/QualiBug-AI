from __future__ import annotations

from ai_test_asset_center.experiment_protocols import compile_family_protocol
from ai_test_asset_center.validation_obligation_expander import (
    expand_validation_obligation,
)


def test_expand_validation_includes_openapi_query_parameters() -> None:
    operation = {
        "id": "op-search",
        "method": "GET",
        "path": "/api/users/search",
        "read_write": "read",
        "parameters": [
            {
                "name": "userId",
                "in": "query",
                "required": True,
                "schema": {"type": "string", "minLength": 2},
                "example": "ab",
            },
        ],
    }
    obligation = {
        "obligation_id": "obl-query",
        "risk_family": "validation",
        "property": {
            "template": "single_dimension_mutation",
            "operation_ref": "op-search",
            "actor_ref": "actor-public",
        },
        "required_operations": ["op-search"],
        "required_actors": ["actor-public"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": False},
        "source_refs": [{"source_id": "api-spec"}],
    }

    variants = expand_validation_obligation(obligation, operation=operation)
    assert any(
        item["property"].get("parameter_location") == "query"
        and item["property"].get("field") == "userId"
        for item in variants
    )
    query_variant = next(
        item
        for item in variants
        if item["property"].get("parameter_location") == "query"
        and item["property"].get("validation_constraint") == "minLength"
    )
    protocol = compile_family_protocol(
        risk_family="validation",
        operation=operation,
        operation_ref="op-search",
        control_actor_ref="actor-public",
        treatment_actor_ref="actor-public",
        property_spec=query_variant["property"],
    )
    assert protocol["status"] == "COMPILED"
    assert "userId" in protocol["treatment_plan"][0].get("query", {})
    assert protocol["treatment_plan"][0]["mutation"]["parameter_location"] == "query"


def test_expand_validation_nested_body_userid_remains_reachable() -> None:
    operation = {
        "id": "op-filter",
        "method": "POST",
        "path": "/api/orders/filter",
        "read_write": "write",
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["filter"],
                        "properties": {
                            "filter": {
                                "type": "object",
                                "required": ["userId"],
                                "properties": {
                                    "userId": {
                                        "type": "string",
                                        "minLength": 2,
                                    },
                                },
                            },
                        },
                    },
                    "example": {"filter": {"userId": "ab"}},
                }
            }
        },
    }
    obligation = {
        "obligation_id": "obl-nested-user",
        "risk_family": "validation",
        "property": {
            "template": "single_dimension_mutation",
            "operation_ref": "op-filter",
            "actor_ref": "actor-public",
        },
        "required_operations": ["op-filter"],
        "required_actors": ["actor-public"],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": False},
        "source_refs": [{"source_id": "api-spec"}],
    }

    variants = expand_validation_obligation(obligation, operation=operation)
    assert any(
        item["property"].get("field_path") == "filter.userId"
        for item in variants
    )
