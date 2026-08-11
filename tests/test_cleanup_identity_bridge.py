from __future__ import annotations


def test_strict_identity_bridge_rejects_arbitrary_nested_id() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder_strict import (
        observed_resource_identity,
    )

    assert observed_resource_identity(
        {"owner": {"id": "owner-9"}},
        identity_column="order_id",
    ) == ""


def test_strict_identity_bridge_accepts_resource_envelope_id() -> None:
    from ai_test_asset_center.cleanup_adapter_ladder_strict import (
        observed_resource_identity,
    )

    assert observed_resource_identity(
        {"result": {"id": "order-9"}},
        identity_column="order_id",
    ) == "order-9"
