from __future__ import annotations


def test_entity_qualified_id_uses_structural_collection_stem() -> None:
    from ai_test_asset_center.real_id_resolver import body_field_collection_paths

    assert body_field_collection_paths("widget_id") == [
        "/api/widgets",
        "/api/widget",
    ]
    assert body_field_collection_paths("patient_id") == [
        "/api/patients",
        "/api/patient",
    ]


def test_natural_key_does_not_guess_a_domain_collection() -> None:
    from ai_test_asset_center.real_id_resolver import body_field_collection_paths

    assert body_field_collection_paths("sku") == []
    assert body_field_collection_paths("code") == []


def test_parameter_aliases_do_not_cross_business_domains() -> None:
    from ai_test_asset_center.real_id_resolver import param_field_candidates

    coupon = {value.lower() for value in param_field_candidates("couponId")}
    user = {value.lower() for value in param_field_candidates("userId")}

    assert "code" not in coupon
    assert "sku" not in coupon
    assert "account_id" not in user
    assert "customer_id" not in user
    assert "id" in coupon
    assert "id" in user


def test_alternate_collection_paths_do_not_inject_catalog_or_user_vocab() -> None:
    from ai_test_asset_center.real_id_resolver import alternate_collection_paths

    inventory = alternate_collection_paths("/api/inventory/{sku}")
    assert "/api/products" not in inventory
    assert "/api/materials" not in inventory
    assert "/api/catalog" not in inventory

    custom = alternate_collection_paths("/api/widgets/{widgetId}/activate")
    assert "/api/widgets" in custom
    assert "/api/users" not in custom
    assert "/api/accounts" not in custom
