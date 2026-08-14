from __future__ import annotations


def _entity() -> dict:
    return {
        "id": "entity-address",
        "name": "address",
        "identity_fields": ["id"],
        "collection_path": "/api/addresses",
    }


def _create(op_id: str) -> dict:
    return {
        "id": op_id,
        "method": "POST",
        "path": "/api/addresses",
        "request_example": {"line1": "fixture"},
    }


def test_duplicate_structural_create_operations_are_ambiguous() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _resolve_create_operation_candidates,
    )

    candidates = _resolve_create_operation_candidates(
        {
            "entities": [_entity()],
            "operations": [_create("create-a"), _create("create-b")],
            "relations": [],
        },
        _entity(),
    )

    assert [row["id"] for row in candidates] == ["create-a", "create-b"]
    assert all("entity_collection" in row["_create_authorities"] for row in candidates)


def test_produces_relation_without_source_refs_is_not_create_authority() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _resolve_create_operation_candidates,
    )

    operation = {
        "id": "register-address",
        "method": "POST",
        "path": "/api/register-address",
        "request_example": {"line1": "fixture"},
    }
    candidates = _resolve_create_operation_candidates(
        {
            "entities": [_entity()],
            "operations": [operation],
            "relations": [
                {
                    "relation_type": "produces",
                    "from_ref": "register-address",
                    "to_ref": "entity-address",
                    "status": "accepted",
                }
            ],
        },
        _entity(),
    )

    assert candidates == []


def test_source_backed_produces_relation_authorizes_non_collection_create() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _resolve_create_operation_candidates,
    )

    operation = {
        "id": "register-address",
        "method": "POST",
        "path": "/api/register-address",
        "request_example": {"line1": "fixture"},
    }
    candidates = _resolve_create_operation_candidates(
        {
            "entities": [_entity()],
            "operations": [operation],
            "relations": [
                {
                    "relation_type": "produces",
                    "from_ref": "register-address",
                    "to_ref": "entity-address",
                    "source_refs": [{"source_id": "prd"}],
                    "status": "accepted",
                }
            ],
        },
        _entity(),
    )

    assert len(candidates) == 1
    assert candidates[0]["id"] == "register-address"
    assert candidates[0]["_create_authorities"] == ["explicit_produces_relation"]


def test_two_permitted_fixture_actors_are_ambiguous_not_first() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _create_actor_authority,
    )

    behavior_ir = {
        "actors": [
            {"id": "actor-a", "role": "member"},
            {"id": "actor-b", "role": "member"},
        ],
        "relations": [
            {
                "relation_type": "permits",
                "operation_ref": "create-address",
                "actor_ref": "actor-a",
            },
            {
                "relation_type": "permits",
                "operation_ref": "create-address",
                "actor_ref": "actor-b",
            },
        ],
    }

    actor, authority, eligible = _create_actor_authority(
        create_operation=_create("create-address"),
        behavior_ir=behavior_ir,
        actor_refs=["actor-a", "actor-b"],
    )

    assert actor == ""
    assert authority == "operation_permits_ambiguous"
    assert eligible == ["actor-a", "actor-b"]


def test_caller_actor_restriction_can_make_permits_unique() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _create_actor_authority,
    )

    behavior_ir = {
        "actors": [
            {"id": "actor-a", "role": "member"},
            {"id": "actor-b", "role": "member"},
        ],
        "relations": [
            {"relation_type": "permits", "operation_ref": "create-address", "actor_ref": "actor-a"},
            {"relation_type": "permits", "operation_ref": "create-address", "actor_ref": "actor-b"},
        ],
    }

    actor, authority, eligible = _create_actor_authority(
        create_operation=_create("create-address"),
        behavior_ir=behavior_ir,
        actor_refs=["actor-b"],
    )

    assert actor == "actor-b"
    assert authority == "operation_permits_unique"
    assert eligible == ["actor-b"]


def test_single_collection_create_is_preferred_over_flow_create() -> None:
    """A canonical collection POST wins over a specialized produces-relation
    create (direct create vs. from-cart flow) — never a first-item pick.

    Duplicate collection POSTs and multiple produces-only creates stay
    ambiguous and fail closed; only a UNIQUE collection POST disambiguates.
    """
    from ai_test_asset_center.multi_level_dependency_chain import (
        _resolve_create_operation_candidates,
    )

    entity = _entity()
    behavior_ir = {
        "entities": [entity],
        "operations": [
            _create("create-address"),
            {
                "id": "create-address-from-cart",
                "method": "POST",
                "path": "/api/addresses/from-cart",
                "request_example": {"line1": "fixture"},
            },
        ],
        "relations": [
            {
                "relation_type": "produces",
                "from_ref": "create-address-from-cart",
                "to_ref": "entity-address",
                "source_refs": [{"source_id": "prd"}],
                "status": "accepted",
            }
        ],
    }

    candidates = _resolve_create_operation_candidates(behavior_ir, entity)
    # Both authorities are present (collection + produces), so the caller-level
    # disambiguation must reduce to exactly the collection POST.
    collection = [
        row
        for row in candidates
        if "entity_collection" in row["_create_authorities"]
    ]
    assert len(collection) == 1
    assert collection[0]["id"] == "create-address"
