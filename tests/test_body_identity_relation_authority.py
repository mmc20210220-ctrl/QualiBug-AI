from __future__ import annotations


def _row() -> dict:
    return {
        "target": "addressId",
        "status": "runtime_resolvable",
        "source_priority": "same_actor_list_read",
        "resolver_operations": [
            {
                "operation_ref": "list-addresses",
                "method": "GET",
                "path": "/api/addresses",
            }
        ],
        "body_template_paths": ["addressId"],
        "fixture_setup": {"operation_ref": "create-address"},
        "value_fingerprint": "",
    }


def test_identity_name_plus_real_get_is_not_relationship_authority() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _govern_body_identity_relations,
    )

    operation = {
        "id": "create-order",
        "method": "POST",
        "path": "/api/orders",
        "request_schema": {
            "type": "object",
            "properties": {
                "addressId": {"type": "string"},
            },
        },
    }

    governed = _govern_body_identity_relations([_row()], operation=operation)[0]

    assert governed["status"] == "blocked"
    assert governed["blocked_reason"] == (
        "BODY_IDENTITY_RELATION_NOT_SOURCE_DECLARED"
    )
    assert governed["resolver_operations"] == []
    assert "fixture_setup" not in governed


def test_explicit_schema_foreign_key_allows_body_identity_resolver() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _govern_body_identity_relations,
    )

    operation = {
        "id": "create-order",
        "method": "POST",
        "path": "/api/orders",
        "request_schema": {
            "type": "object",
            "properties": {
                "addressId": {
                    "type": "string",
                    "x-foreign-key": True,
                },
            },
        },
    }

    governed = _govern_body_identity_relations([_row()], operation=operation)[0]

    assert governed["status"] == "runtime_resolvable"
    assert governed["body_identity_relation_authority"] == (
        "request_schema_foreign_key"
    )
    assert governed["resolver_operations"]


def test_explicit_field_dictionary_fk_is_also_relationship_authority() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _body_identity_relation_authority,
    )

    allowed, authority = _body_identity_relation_authority(
        {
            "field_dictionary": [
                {
                    "field": "addressId",
                    "foreign_key": True,
                }
            ]
        },
        ["addressId"],
    )

    assert allowed is True
    assert authority == "field_dictionary_foreign_key"


def test_path_identity_is_not_reclassified_as_body_relation() -> None:
    from ai_test_asset_center.runtime_binding_graph import (
        _govern_body_identity_relations,
    )

    row = {
        "target": "orderId",
        "status": "runtime_resolvable",
        "source_priority": "same_actor_list_read",
        "resolver_operations": [{"operation_ref": "list-orders"}],
        "value_fingerprint": "",
    }
    operation = {
        "id": "patch-order",
        "method": "PATCH",
        "path": "/api/orders/{orderId}",
    }

    assert _govern_body_identity_relations([row], operation=operation) == [row]


def test_ownership_identity_keeps_its_existing_authority_channel(monkeypatch) -> None:
    import ai_test_asset_center.runtime_binding_graph as graph

    monkeypatch.setattr(
        graph._authority,
        "_ownership_params_declared_on_operation",
        lambda operation: ["userId"],
    )
    row = {
        "target": "userId",
        "status": "runtime_resolvable",
        "source_priority": "ownership_identity_param",
        "body_template_paths": ["userId"],
        "value_fingerprint": "",
    }

    assert graph._govern_body_identity_relations(
        [row],
        operation={"id": "op", "method": "POST", "path": "/api/cart"},
    ) == [row]
