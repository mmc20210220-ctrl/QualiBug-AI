from __future__ import annotations


def _behavior(field_schema: dict) -> tuple[dict, dict]:
    operation = {
        "id": "create-payment",
        "method": "POST",
        "path": "/api/payments",
        "request_example": {"orderId": "{orderId}", "amount": 10},
        "request_schema": {
            "type": "object",
            "properties": {
                "orderId": field_schema,
                "amount": {"type": "number"},
            },
        },
    }
    return operation, {
        "entities": [
            {"id": "entity-order", "name": "order", "identity_fields": ["id"]},
            {"id": "entity-address", "name": "address", "identity_fields": ["id"]},
        ],
        "operations": [operation],
        "relations": [],
    }


def test_order_id_spelling_is_not_money_subject_authority() -> None:
    from ai_test_asset_center.money_precondition_chain import (
        _source_declared_subject_pairs,
    )

    operation, behavior_ir = _behavior({"type": "string"})

    assert _source_declared_subject_pairs(operation, behavior_ir) == []


def test_boolean_fk_without_target_is_not_money_subject_authority() -> None:
    from ai_test_asset_center.money_precondition_chain import (
        _source_declared_subject_pairs,
    )

    operation, behavior_ir = _behavior(
        {"type": "string", "x-foreign-key": True}
    )

    assert _source_declared_subject_pairs(operation, behavior_ir) == []


def test_explicit_target_selects_money_subject_entity() -> None:
    from ai_test_asset_center.money_precondition_chain import (
        _source_declared_subject_pairs,
    )

    operation, behavior_ir = _behavior(
        {"type": "string", "x-entity-ref": "entity-order"}
    )

    assert _source_declared_subject_pairs(operation, behavior_ir) == [
        ("entity-order", "orderId")
    ]


def test_misleading_order_id_name_obeys_explicit_address_target() -> None:
    from ai_test_asset_center.money_precondition_chain import (
        _source_declared_subject_pairs,
    )

    operation, behavior_ir = _behavior(
        {"type": "string", "x-entity-ref": "entity-address"}
    )

    assert _source_declared_subject_pairs(operation, behavior_ir) == [
        ("entity-address", "orderId")
    ]
