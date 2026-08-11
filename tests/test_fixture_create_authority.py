from __future__ import annotations


def test_dependency_resolver_requires_real_operation_identity() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _authoritative_resolver_paths,
    )

    operations = {
        "list-users": {
            "id": "list-users",
            "method": "GET",
            "path": "/api/users",
        }
    }

    assert _authoritative_resolver_paths(
        [{"operation_ref": "missing", "method": "GET", "path": "/api/users"}],
        operations,
    ) == []
    assert _authoritative_resolver_paths(
        [
            {
                "operation_ref": "list-users",
                "method": "GET",
                "path": "/api/users",
            }
        ],
        operations,
    ) == ["/api/users"]


def test_auto_create_collects_all_source_post_candidates_instead_of_first() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _auto_create_candidates,
    )

    operations = {
        "list-items": {
            "id": "list-items",
            "method": "GET",
            "path": "/api/items",
        },
        "create-items": {
            "id": "create-items",
            "method": "POST",
            "path": "/api/items",
        },
        "admin-create-items": {
            "id": "admin-create-items",
            "method": "POST",
            "path": "/api/items/admin",
        },
    }
    binding = {
        "resolver_operations": [
            {
                "operation_ref": "list-items",
                "method": "GET",
                "path": "/api/items",
            }
        ]
    }

    assert set(_auto_create_candidates(binding, operations)) == {
        "create-items",
        "admin-create-items",
    }


def test_multiple_fixture_actors_are_not_source_order_authority() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _fixture_actor_authority,
    )

    actors = {
        "actor-a": {"id": "actor-a", "role": "member"},
        "actor-b": {"id": "actor-b", "role": "member"},
    }

    assert _fixture_actor_authority({}, actors) == ""
    assert _fixture_actor_authority(
        {"fixture_owner_actor_ref": "actor-b"}, actors
    ) == "actor-b"


def test_fabricated_dependency_target_path_is_not_create_authority() -> None:
    from ai_test_asset_center.experiment_fixture_materializer import (
        _auto_create_candidates,
    )

    operations = {
        "create-users": {
            "id": "create-users",
            "method": "POST",
            "path": "/api/users",
        }
    }

    assert _auto_create_candidates(
        {"target_path": "/{user_id}", "resolver_operations": []},
        operations,
    ) == []
