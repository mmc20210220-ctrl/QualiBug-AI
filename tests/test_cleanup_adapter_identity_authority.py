from __future__ import annotations


def test_observed_resource_identity_rejects_nested_related_object_id() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import observed_resource_identity

    assert observed_resource_identity(
        {"user": {"id": "wrong-user"}, "status": "CREATED"},
        identity_column="order_id",
    ) == ""


def test_observed_resource_identity_accepts_standard_envelope_generic_id() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder import observed_resource_identity

    assert observed_resource_identity(
        {"data": {"id": "order-123"}},
        identity_column="order_id",
    ) == "order-123"
