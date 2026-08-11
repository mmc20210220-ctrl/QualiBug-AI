from __future__ import annotations


def _base_operations() -> list[dict]:
    return [
        {
            "id": "create-order",
            "method": "POST",
            "path": "/api/orders",
            "request_example": {"name": "fixture"},
        },
        {
            "id": "delete-order",
            "method": "DELETE",
            "path": "/api/orders/{id}",
        },
    ]


def _permits() -> dict:
    return {
        "relation_type": "permits",
        "operation_ref": "create-order",
        "actor_ref": "actor-owner",
    }


def test_structural_collection_post_is_fixture_create_authority() -> None:
    from ai_test_asset_center.runtime_binding_graph import _declared_fixture_setup

    current = {
        "id": "patch-order",
        "method": "PATCH",
        "path": "/api/orders/{id}",
        "entity_refs": ["entity-order"],
    }
    behavior_ir = {
        "operations": [current, *_base_operations()],
        "relations": [_permits()],
    }

    setup = _declared_fixture_setup(
        current,
        target="id",
        behavior_ir=behavior_ir,
    )

    assert setup["operation_ref"] == "create-order"
    assert setup["path"] == "/api/orders"
    assert setup["actor_refs"] == ["actor-owner"]
    assert setup["create_authorities"] == ["structural_collection"]


def test_action_path_cannot_borrow_sibling_post_without_produces_relation() -> None:
    from ai_test_asset_center.runtime_binding_graph import _declared_fixture_setup

    current = {
        "id": "confirm-order",
        "method": "POST",
        "path": "/api/orders/confirm/{id}",
        "entity_refs": ["entity-order"],
    }
    behavior_ir = {
        "operations": [current, *_base_operations()],
        "relations": [_permits()],
    }

    assert _declared_fixture_setup(
        current,
        target="id",
        behavior_ir=behavior_ir,
    ) == {}


def test_explicit_produces_relation_can_authorize_non_collection_create() -> None:
    from ai_test_asset_center.runtime_binding_graph import _declared_fixture_setup

    current = {
        "id": "confirm-order",
        "method": "POST",
        "path": "/api/orders/confirm/{id}",
        "entity_refs": ["entity-order"],
    }
    behavior_ir = {
        "operations": [current, *_base_operations()],
        "relations": [
            _permits(),
            {
                "relation_type": "produces",
                "from_ref": "create-order",
                "to_ref": "entity-order",
                "source_refs": [{"source_id": "api-doc"}],
            },
        ],
    }

    setup = _declared_fixture_setup(
        current,
        target="id",
        behavior_ir=behavior_ir,
    )

    assert setup["operation_ref"] == "create-order"
    assert setup["create_authorities"] == ["explicit_produces_relation"]


def test_two_viable_create_operations_are_ambiguous_not_source_order() -> None:
    from ai_test_asset_center.runtime_binding_graph import _declared_fixture_setup

    current = {
        "id": "patch-order",
        "method": "PATCH",
        "path": "/api/orders/{id}",
        "entity_refs": ["entity-order"],
    }
    operations = [
        current,
        *_base_operations(),
        {
            "id": "create-order-2",
            "method": "POST",
            "path": "/api/orders",
            "request_example": {"name": "fixture-2"},
        },
    ]
    behavior_ir = {
        "operations": operations,
        "relations": [
            _permits(),
            {
                "relation_type": "permits",
                "operation_ref": "create-order-2",
                "actor_ref": "actor-owner",
            },
        ],
    }

    assert _declared_fixture_setup(
        current,
        target="id",
        behavior_ir=behavior_ir,
    ) == {}
