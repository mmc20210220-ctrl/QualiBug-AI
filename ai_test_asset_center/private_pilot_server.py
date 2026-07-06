from __future__ import annotations

"""Patched private pilot server entrypoint.

The legacy private pilot service is intentionally kept stable because it is a
large HTTP entrypoint. This wrapper installs the stricter backend customer
delivery gate before delegating to the original server runner.
"""

from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks

PATCH_SOURCE = "ai_test_asset_center.private_pilot_server"


def _inject_delivery_gate_patch_status(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    status = customer_delivery_gate_patch_status()
    payload["customer_delivery_gate_patch"] = status
    data = payload.get("data")
    if isinstance(data, dict):
        data["customer_delivery_gate_patch"] = status
        data_contract = data.get("data_contract")
        if isinstance(data_contract, dict):
            data_contract["customer_delivery_gate_patch"] = status
        delivery_tracks = data.get("delivery_tracks")
        if isinstance(delivery_tracks, dict):
            delivery_tracks["customer_delivery_gate_patch"] = status
    return payload


def install_customer_delivery_gate_patch() -> None:
    """Route legacy delivery-track partitioning and response diagnostics through the backend gate."""
    if getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False):
        return

    original_partition = getattr(_service, "_partition_delivery_tracks", None)
    original_normalizer = getattr(_service, "_normalize_command_center_envelope", None)

    def _strict_partition_delivery_tracks(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        safe_items = [item for item in items if isinstance(item, dict)]
        return split_customer_delivery_tracks(safe_items)

    def _strict_normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = original_normalizer(payload) if callable(original_normalizer) else payload
        return _inject_delivery_gate_patch_status(normalized)

    _service._ORIGINAL_PARTITION_DELIVERY_TRACKS = original_partition  # type: ignore[attr-defined]
    _service._ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE = original_normalizer  # type: ignore[attr-defined]
    _service._partition_delivery_tracks = _strict_partition_delivery_tracks  # type: ignore[attr-defined]
    _service._normalize_command_center_envelope = _strict_normalize_command_center_envelope  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCHED = True  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def customer_delivery_gate_patch_status() -> dict[str, Any]:
    """Return runtime diagnostics for the delivery-gate patch."""
    return {
        "patched": bool(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False)),
        "source": str(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCH_SOURCE", "")),
        "has_original_partition": bool(getattr(_service, "_ORIGINAL_PARTITION_DELIVERY_TRACKS", None)),
        "has_original_normalizer": bool(getattr(_service, "_ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE", None)),
        "active_partition_name": getattr(getattr(_service, "_partition_delivery_tracks", None), "__name__", ""),
        "active_normalizer_name": getattr(getattr(_service, "_normalize_command_center_envelope", None), "__name__", ""),
    }


def restore_customer_delivery_gate_patch() -> None:
    """Restore the original partition and normalizer functions for isolated tests or diagnostics."""
    original_partition = getattr(_service, "_ORIGINAL_PARTITION_DELIVERY_TRACKS", None)
    original_normalizer = getattr(_service, "_ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE", None)
    if original_partition is not None:
        _service._partition_delivery_tracks = original_partition  # type: ignore[attr-defined]
    if original_normalizer is not None:
        _service._normalize_command_center_envelope = original_normalizer  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCHED = False  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_PARTITION_DELIVERY_TRACKS = None  # type: ignore[attr-defined]
    _service._ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE = None  # type: ignore[attr-defined]


def run_server() -> None:
    install_customer_delivery_gate_patch()
    _service.run_server()
