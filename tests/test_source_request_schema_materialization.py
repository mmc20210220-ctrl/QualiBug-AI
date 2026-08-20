from __future__ import annotations

from ai_test_asset_center.source_request_schema_materialization import (
    install_source_request_schema_materialization,
    materialize_authoritative_request_body,
)


def _operation(schema: dict) -> dict:
    return {
        "id": "op_update_resource",
        "method": "PATCH",
        "path": "/api/resources/{id}",
        "request_schema": {
            "content": {
                "application/json": {
                    "schema": schema,
                }
            }
        },
    }


def test_materializes_required_const_default_and_single_enum_fields() -> None:
    body, receipt = materialize_authoritative_request_body(
        _operation(
            {
                "type": "object",
                "required": ["mode", "enabled", "limit"],
                "properties": {
                    "mode": {"type": "string", "enum": ["strict"]},
                    "enabled": {"type": "boolean", "const": True},
                    "limit": {"type": "integer", "default": 5},
                },
            }
        )
    )

    assert body == {"mode": "strict", "enabled": True, "limit": 5}
    assert receipt["status"] == "MATERIALIZED"
    assert {row["authority"] for row in receipt["fields"]} == {
        "single_enum",
        "const",
        "default",
    }


def test_materializes_nested_required_object_only_from_attested_values() -> None:
    body, receipt = materialize_authoritative_request_body(
        _operation(
            {
                "type": "object",
                "required": ["policy"],
                "properties": {
                    "policy": {
                        "type": "object",
                        "required": ["state", "version"],
                        "properties": {
                            "state": {"type": "string", "const": "ACTIVE"},
                            "version": {"type": "integer", "enum": [2]},
                        },
                    }
                },
            }
        )
    )

    assert body == {"policy": {"state": "ACTIVE", "version": 2}}
    assert receipt["status"] == "MATERIALIZED"


def test_ambiguous_required_field_remains_unavailable() -> None:
    body, receipt = materialize_authoritative_request_body(
        _operation(
            {
                "type": "object",
                "required": ["name", "state"],
                "properties": {
                    "name": {"type": "string"},
                    "state": {"type": "string", "enum": ["ACTIVE"]},
                },
            }
        )
    )

    assert body == {}
    assert receipt["status"] == "UNAVAILABLE"
    assert receipt["reason"] == "required_request_fields_not_deterministic"


def test_optional_only_schema_is_not_arbitrarily_materialized() -> None:
    body, receipt = materialize_authoritative_request_body(
        _operation(
            {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "default": "strict"},
                    "enabled": {"type": "boolean", "const": True},
                },
            }
        )
    )

    assert body == {}
    assert receipt["status"] == "UNAVAILABLE"


def test_source_default_that_conflicts_with_enum_is_rejected() -> None:
    body, receipt = materialize_authoritative_request_body(
        _operation(
            {
                "type": "object",
                "required": ["state"],
                "properties": {
                    "state": {
                        "type": "string",
                        "default": "DELETED",
                        "enum": ["ACTIVE", "PAUSED"],
                    }
                },
            }
        )
    )

    assert body == {}
    assert receipt["status"] == "UNAVAILABLE"


def test_installer_rebinds_main_compiler_request_body_authority() -> None:
    # The stable compiler imports the helper by value; this regression ensures
    # the additive installer reaches that local binding rather than merely
    # replacing the support-module attribute.
    from ai_test_asset_center import experiment_compiler_obligation_core as core

    install_source_request_schema_materialization()
    body = core._source_request_example(
        _operation(
            {
                "type": "object",
                "required": ["state"],
                "properties": {
                    "state": {"type": "string", "enum": ["ACTIVE"]},
                },
            }
        )
    )

    assert body == {"state": "ACTIVE"}
