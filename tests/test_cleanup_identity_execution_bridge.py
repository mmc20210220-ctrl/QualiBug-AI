from __future__ import annotations


def test_execution_bridge_does_not_use_nested_related_id() -> None:
    from ai_test_asset_center._cleanup_identity_execution_bridge import (
        identity_from_governed_write,
    )

    assert identity_from_governed_write(
        {"identity_column": "order_id"},
        {"write": {"body": {"owner": {"id": "wrong-owner"}}}},
    ) == ""


def test_execution_bridge_accepts_tracked_created_identity() -> None:
    from ai_test_asset_center._cleanup_identity_execution_bridge import (
        identity_from_governed_write,
    )

    assert identity_from_governed_write(
        {"identity_column": "order_id"},
        {"observed_created_identity": "order-77"},
    ) == "order-77"
