"""Translate explicitly typed external-signal rows into the event contract overlay input.

``external_signal_requests`` is already an immutable scan-context field. Reusing it avoids a
second HTTP contract while keeping admission strict: only rows explicitly typed as a formal
event contract are considered. Ordinary webhooks, monitoring signals or integration requests
remain outside the formal event authority.
"""
from __future__ import annotations

import copy
from typing import Any

from .scan_event_contract_overlay import overlay_scan_event_contracts

_ALLOWED_TYPES = frozenset({
    "formal_event_contract",
    "event_delivery_contract",
    "source_declared_event_observation",
})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_formal_event_contract(row: dict[str, Any]) -> bool:
    schema = _text(row.get("schema_version"))
    explicit_type = _text(
        row.get("signal_type")
        or row.get("contract_type")
        or row.get("kind")
        or row.get("type")
    ).lower()
    return bool(
        schema.startswith("qualibug.formal-event-contract")
        or explicit_type in _ALLOWED_TYPES
    )


def overlay_scan_event_contracts_with_external_signals(
    asset: dict[str, Any] | None,
    campaign_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    context = copy.deepcopy(_dict(campaign_context))
    existing = [
        copy.deepcopy(row)
        for row in _list(context.get("event_formal_contracts"))
        if isinstance(row, dict)
    ]
    external = [
        copy.deepcopy(row)
        for row in _list(context.get("external_signal_requests"))
        if isinstance(row, dict) and _is_formal_event_contract(row)
    ]
    if external:
        context["event_formal_contracts"] = [*existing, *external]
    merged, receipt = overlay_scan_event_contracts(
        asset,
        campaign_context=context,
    )
    receipt = dict(receipt)
    receipt["external_signal_request_count"] = len(
        [
            row
            for row in _list(context.get("external_signal_requests"))
            if isinstance(row, dict)
        ]
    )
    receipt["typed_external_event_contract_count"] = len(external)
    merged["scan_event_contract_overlay_receipt"] = receipt
    return merged, receipt


__all__ = ["overlay_scan_event_contracts_with_external_signals"]
