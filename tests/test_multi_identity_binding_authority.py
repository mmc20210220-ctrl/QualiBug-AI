from __future__ import annotations


def test_one_generic_id_cannot_fill_two_different_path_identities() -> None:
    from ai_test_asset_center.real_id_resolver import bind_entity_fields

    bindings = bind_entity_fields(
        {"id": "U1"},
        "/users/{userId}/addresses/{addressId}",
    )

    assert "userId" not in bindings
    assert "addressId" not in bindings


def test_exact_parent_identity_allows_generic_id_for_one_remaining_child() -> None:
    from ai_test_asset_center.real_id_resolver import bind_entity_fields

    bindings = bind_entity_fields(
        {"user_id": "U1", "id": "A1"},
        "/users/{userId}/addresses/{addressId}",
    )

    assert bindings["userId"] == "U1"
    assert bindings["addressId"] == "A1"


def test_both_explicit_multi_identity_fields_remain_authoritative() -> None:
    from ai_test_asset_center.real_id_resolver import bind_entity_fields

    bindings = bind_entity_fields(
        {"user_id": "U1", "address_id": "A1"},
        "/users/{userId}/addresses/{addressId}",
    )

    assert bindings["userId"] == "U1"
    assert bindings["addressId"] == "A1"


def test_single_identity_path_keeps_existing_generic_id_compatibility() -> None:
    from ai_test_asset_center.real_id_resolver import bind_entity_fields

    bindings = bind_entity_fields(
        {"id": "O1"},
        "/orders/{orderId}",
    )

    assert bindings["orderId"] == "O1"
