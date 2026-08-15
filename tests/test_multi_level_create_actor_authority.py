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


def test_two_distinct_permitted_roles_are_ambiguous_not_first() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _create_actor_authority,
    )

    behavior_ir = {
        "actors": [
            {"id": "actor-a", "role": "member"},
            {"id": "actor-b", "role": "seller"},
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


def test_caller_role_restriction_can_make_permits_unique() -> None:
    from ai_test_asset_center.multi_level_dependency_chain import (
        _create_actor_authority,
    )

    behavior_ir = {
        "actors": [
            {"id": "actor-a", "role": "member"},
            {"id": "actor-b", "role": "seller"},
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


def test_business_role_wins_over_management_role() -> None:
    """A business role is the subject-establishment initiator; a management
    role (admin/operator/auditor) is a fallback authority, never a tie-breaker
    that makes a single-business-role chain look ambiguous.
    """
    from ai_test_asset_center.multi_level_dependency_chain import (
        _create_actor_authority,
    )

    behavior_ir = {
        "actors": [
            {"id": "actor-buyer", "role": "buyer"},
            {"id": "actor-admin", "role": "admin"},
        ],
        "relations": [
            {"relation_type": "permits", "operation_ref": "create-address", "actor_ref": "actor-buyer"},
            {"relation_type": "permits", "operation_ref": "create-address", "actor_ref": "actor-admin"},
        ],
    }

    actor, authority, eligible = _create_actor_authority(
        create_operation=_create("create-address"),
        behavior_ir=behavior_ir,
        actor_refs=[],
    )

    # buyer (business role) is the unique subject creator; admin (management)
    # is excluded from the tie-breaker.
    assert actor == "actor-buyer"
    assert authority == "operation_permits_unique"
    assert eligible == ["actor-buyer"]


def test_role_and_account_actors_of_same_role_collapse_to_one_authority() -> None:
    """Role-level and account-level actors of one role share a role_key.

    The IR represents one declared role twice: a role-level actor node (no
    account_ref) plus one node per runtime account (account_ref present). Both
    get a permits edge, which previously inflated the declared actor list and
    made a single-role authority look ambiguous (or missing when the caller
    held the other spelling). Collapse by role_key, preferring the account-
    bound actor, so a single role resolves to one executable authority.
    """
    from ai_test_asset_center.multi_level_dependency_chain import (
        _create_actor_authority,
    )

    behavior_ir = {
        "actors": [
            {"id": "role-buyer", "role": "buyer", "role_key": "buyer"},
            {"id": "account-buyer01", "role": "buyer", "role_key": "buyer", "account_ref": "buyer01"},
        ],
        "relations": [
            {"relation_type": "permits", "operation_ref": "create-address", "actor_ref": "role-buyer"},
            {"relation_type": "permits", "operation_ref": "create-address", "actor_ref": "account-buyer01"},
        ],
    }

    actor, authority, eligible = _create_actor_authority(
        create_operation=_create("create-address"),
        behavior_ir=behavior_ir,
        actor_refs=[],
    )

    # Collapses to the account-bound actor of the single buyer role.
    assert actor == "account-buyer01"
    assert authority == "operation_permits_unique"
    assert eligible == ["account-buyer01"]


def test_role_and_account_actors_of_same_role_with_role_caller_still_unique() -> None:
    """The caller may hold the role-level spelling while the declared list
    carries both spellings; role-collapse must still resolve a unique actor.
    """
    from ai_test_asset_center.multi_level_dependency_chain import (
        _create_actor_authority,
    )

    behavior_ir = {
        "actors": [
            {"id": "role-buyer", "role": "buyer", "role_key": "buyer"},
            {"id": "account-buyer01", "role": "buyer", "role_key": "buyer", "account_ref": "buyer01"},
        ],
        "relations": [
            {"relation_type": "permits", "operation_ref": "create-address", "actor_ref": "role-buyer"},
            {"relation_type": "permits", "operation_ref": "create-address", "actor_ref": "account-buyer01"},
        ],
    }

    actor, authority, eligible = _create_actor_authority(
        create_operation=_create("create-address"),
        behavior_ir=behavior_ir,
        actor_refs=["role-buyer"],
    )

    assert actor == "account-buyer01"
    assert authority == "operation_permits_unique"
    assert eligible == ["account-buyer01"]


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
