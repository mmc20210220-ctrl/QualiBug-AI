from __future__ import annotations

"""Patched private pilot server entrypoint.

The legacy private pilot service is intentionally kept stable because it is a
large HTTP entrypoint. This wrapper installs the stricter backend customer
delivery gate before delegating to the original server runner.
"""

from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks


def install_customer_delivery_gate_patch() -> None:
    """Route legacy delivery-track partitioning through the backend gate."""
    if getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False):
        return

    def _strict_partition_delivery_tracks(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        safe_items = [item for item in items if isinstance(item, dict)]
        return split_customer_delivery_tracks(safe_items)

    _service._partition_delivery_tracks = _strict_partition_delivery_tracks  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCHED = True  # type: ignore[attr-defined]


def run_server() -> None:
    install_customer_delivery_gate_patch()
    _service.run_server()
