"""Behavior IR projection hygiene: produces via overlap + nested field isolation.

Industry-neutral: no route→table hardcoding. Nested resource fields must not
pollute parent entities; creates without path vocabulary may gain entity_refs /
produces only when request fields uniquely overlap source entity columns.
"""
from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.experiment_compiler_obligation_core import (
    _entity_for_operation,
    _entity_identity_fields,
)


def _field_names(entity: dict) -> set[str]:
    names: set[str] = set()
    for field in entity.get("fields") or []:
        if isinstance(field, dict):
            name = str(field.get("name") or field.get("field") or "").lower()
        else:
            name = str(field).lower()
        if name:
            names.add(name)
    return names


def _build_register_address_ir():
    return build_behavior_ir_from_knowledge_asset(
        {
            "business_objects": [
                {"name": "users", "kind": "business_object", "fields": ["role"]},
                {"name": "addresses", "kind": "business_object"},
            ],
            "data_tables": [
                {
                    "name": "users",
                    "table_id": "table:users",
                    "kind": "resource",
                    "identity_fields": ["id"],
                    "columns": ["id", "email", "name", "password", "phone", "role"],
                    "fields": ["id", "email", "name", "password", "phone", "role"],
                    "field_dictionary": [
                        {"field": "id", "table": "users"},
                        {"field": "email", "table": "users"},
                        {"field": "name", "table": "users"},
                        {"field": "password", "table": "users"},
                        {"field": "phone", "table": "users"},
                        {"field": "role", "table": "users"},
                    ],
                },
                {
                    "name": "addresses",
                    "table_id": "table:addresses",
                    "kind": "resource",
                    "identity_fields": ["id"],
                    "columns": ["id", "user_id", "receiver", "city", "detail"],
                    "fields": ["id", "user_id", "receiver", "city", "detail"],
                    "field_dictionary": [
                        {"field": "id", "table": "addresses"},
                        {"field": "user_id", "table": "addresses"},
                        {"field": "receiver", "table": "addresses"},
                        {"field": "city", "table": "addresses"},
                        {"field": "detail", "table": "addresses"},
                    ],
                },
            ],
        },
        project_id="projection-hygiene",
        api_operations=[
            {
                "operation_id": "register",
                "method": "POST",
                "path": "/api/auth/register",
                "request_example": {
                    "email": "a@b.com",
                    "name": "Ada",
                    "password": "x",
                    "phone": "1",
                },
            },
            {
                "operation_id": "create_address",
                "method": "POST",
                "path": "/api/users/addresses",
                "request_example": {
                    "receiver": "Bob",
                    "city": "SH",
                    "detail": "rd",
                },
            },
        ],
    )


def test_nested_address_fields_do_not_pollute_users_entity() -> None:
    ir = _build_register_address_ir()
    entities = {row["name"]: row for row in ir["entities"]}
    users_fields = _field_names(entities["users"])
    addresses_fields = _field_names(entities["addresses"])

    assert {"city", "receiver", "detail"}.isdisjoint(users_fields)
    assert {"city", "receiver", "detail"} <= addresses_fields
    assert {"email", "name", "id"} <= users_fields


def test_register_gains_entity_refs_and_produces_via_field_overlap() -> None:
    ir = _build_register_address_ir()
    entities = {row["name"]: row for row in ir["entities"]}
    register = next(
        row for row in ir["operations"] if "/register" in str(row.get("path"))
    )

    assert "users" in {
        str(ref).lower() for ref in (register.get("entity_refs") or [])
    }
    produces = [
        row
        for row in ir["relations"]
        if row.get("relation_type") == "produces"
        and (
            row.get("operation_ref") == register.get("id")
            or row.get("from_ref") == register.get("id")
        )
    ]
    assert len(produces) == 1
    assert produces[0]["to_ref"] == entities["users"]["id"]

    # Overlap bind at compiler remains intact.
    bound = _entity_for_operation(register, ir)
    assert (bound.get("name") or "").lower() == "users"


def test_address_create_still_produces_child_entity() -> None:
    ir = _build_register_address_ir()
    entities = {row["name"]: row for row in ir["entities"]}
    address_op = next(
        row for row in ir["operations"] if "addresses" in str(row.get("path"))
    )
    produces = [
        row
        for row in ir["relations"]
        if row.get("relation_type") == "produces"
        and (
            row.get("operation_ref") == address_op.get("id")
            or row.get("from_ref") == address_op.get("id")
        )
    ]
    assert len(produces) == 1
    assert produces[0]["to_ref"] == entities["addresses"]["id"]


def test_ambiguous_overlap_does_not_invent_entity_refs() -> None:
    """Two entities sharing the same ≥2 request columns → fail closed."""
    ir = build_behavior_ir_from_knowledge_asset(
        {
            "data_tables": [
                {
                    "name": "accounts",
                    "kind": "resource",
                    "identity_fields": ["id"],
                    "fields": ["id", "email", "name"],
                    "field_dictionary": [
                        {"field": "id", "table": "accounts"},
                        {"field": "email", "table": "accounts"},
                        {"field": "name", "table": "accounts"},
                    ],
                },
                {
                    "name": "profiles",
                    "kind": "resource",
                    "identity_fields": ["id"],
                    "fields": ["id", "email", "name"],
                    "field_dictionary": [
                        {"field": "id", "table": "profiles"},
                        {"field": "email", "table": "profiles"},
                        {"field": "name", "table": "profiles"},
                    ],
                },
            ],
        },
        project_id="ambiguous-overlap",
        api_operations=[
            {
                "operation_id": "signup",
                "method": "POST",
                "path": "/api/auth/signup",
                "request_example": {"email": "a@b.com", "name": "Ada"},
            }
        ],
    )
    signup = ir["operations"][0]
    assert not list(signup.get("entity_refs") or [])
    assert not [
        row
        for row in ir["relations"]
        if row.get("relation_type") == "produces"
        and (
            row.get("operation_ref") == signup.get("id")
            or row.get("from_ref") == signup.get("id")
        )
    ]


def test_entity_identity_fields_prefer_generic_primary_key() -> None:
    """Unique business IDENTITY columns must not outrank id/uuid/guid for cleanup."""
    fields = _entity_identity_fields(
        {
            "fields": [
                {
                    "name": "email",
                    "semantic_type": "IDENTITY",
                    "identity_role": "primary_key",
                },
                {
                    "name": "id",
                    "semantic_type": "IDENTITY",
                    "identity_role": "primary_key",
                },
            ]
        }
    )
    assert fields[0] == "id"
    assert "email" in fields
