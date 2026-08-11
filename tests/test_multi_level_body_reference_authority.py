from __future__ import annotations


def _ir(*, field_schema: dict) -> dict:
    return {
        "entities": [
            {
                "id": "entity-order",
                "name": "order",
                "identity_fields": ["id"],
                "collection_path": "/api/orders",
            },
            {
                "id": "entity-address",
                "name": "address",
                "identity_fields": ["id"],
                "collection_path": "/api/addresses",
            },
        ],
        "operations": [
            {
                "id": "create-order",
                "method": "POST",
                "path": "/api/orders",
                "entity_refs": ["entity-order"],
                "request_example": {"billingAddressId": "{billingAddressId}"},
                "request_schema": {
                    "type": "object",
                    "properties": {"billingAddressId": field_schema},
                },
            },
            {
                "id": "create-address",
                "method": "POST",
                "path": "/api/addresses",
                "entity_refs": ["entity-address"],
                "request_example": {"line1": "fixture"},
            },
        ],
        "actors": [{"id": "actor-a", "role": "member"}],
        "relations": [
            {
                "relation_type": "permits",
                "operation_ref": "create-order",
                "actor_ref": "actor-a",
                "source_refs": [{"source_id": "api-doc"}],
            },
            {
                "relation_type": "permits",
                "operation_ref": "create-address",
                "actor_ref": "actor-a",
                "source_refs": [{"source_id": "api-doc"}],
            },
        ],
        "_body_reference_operation_ref": "create-order",
    }


def test_nested_field_name_does_not_choose_entity_without_target() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _source_declared_subject_pairs,
    )

    behavior_ir = _ir(field_schema={"type": "string"})

    assert _source_declared_subject_pairs(
        {"billingAddressId": "{billingAddressId}"},
        behavior_ir,
    ) == []


def test_boolean_foreign_key_without_target_is_still_unresolved() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _source_declared_subject_pairs,
    )

    behavior_ir = _ir(
        field_schema={"type": "string", "x-foreign-key": True}
    )

    assert _source_declared_subject_pairs(
        {"billingAddressId": "{billingAddressId}"},
        behavior_ir,
    ) == []


def test_explicit_entity_target_resolves_nested_dependency() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _source_declared_subject_pairs,
    )

    behavior_ir = _ir(
        field_schema={
            "type": "string",
            "x-entity-ref": "entity-address",
        }
    )

    assert _source_declared_subject_pairs(
        {"billingAddressId": "{billingAddressId}"},
        behavior_ir,
    ) == [("entity-address", "billingAddressId")]


def test_misleading_field_name_cannot_override_explicit_target() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _source_declared_subject_pairs,
    )

    behavior_ir = _ir(
        field_schema={
            "type": "string",
            "x-entity-ref": "entity-order",
        }
    )

    assert _source_declared_subject_pairs(
        {"billingAddressId": "{billingAddressId}"},
        behavior_ir,
    ) == [("entity-order", "billingAddressId")]
