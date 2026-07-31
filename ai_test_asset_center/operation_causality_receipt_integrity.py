"""Content integrity for operation-causality transport receipts."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

TRANSPORT_RECEIPT_SCHEMA = "qualibug.operation-causality-transport-receipt.v1"
_REQUIRED_FIELDS = frozenset(
    {
        "schema",
        "receipt_id",
        "assertion_id",
        "causal_scope_fingerprint",
        "operation_ref",
        "treatment_step_id",
        "value_source",
        "preflight_value_fingerprint",
        "transport_value_fingerprint",
        "source_value_fingerprint_match",
        "request_semantics_fingerprint",
        "transport_receipt_id",
        "status_code",
        "campaign_id",
        "execution_id",
        "status",
        "reason_code",
        "transport_reached",
        "raw_causal_value_retained",
        "timestamp_window_attribution_used",
    }
)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _receipt_id(payload: dict[str, Any]) -> str:
    return "causal_transport_" + hashlib.sha256(
        _canonical(payload).encode("utf-8")
    ).hexdigest()[:24]


def seal_operation_causality_transport_receipt(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Seal the exact transport receipt shape used by the runtime."""
    row = deepcopy(_dict(payload))
    row.pop("receipt_id", None)
    if set(row) != (_REQUIRED_FIELDS - {"receipt_id"}):
        raise ValueError("operation_causality_transport_receipt_fields_invalid")
    row["receipt_id"] = _receipt_id(row)
    return row


def validate_operation_causality_transport_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    row = deepcopy(_dict(receipt))
    if set(row) != _REQUIRED_FIELDS:
        raise ValueError("operation_causality_transport_receipt_fields_invalid")
    if _text(row.get("schema")) != TRANSPORT_RECEIPT_SCHEMA:
        raise ValueError("operation_causality_transport_receipt_schema_invalid")
    expected = seal_operation_causality_transport_receipt(row)
    if row != expected:
        raise ValueError("operation_causality_transport_receipt_fingerprint_invalid")
    if row.get("raw_causal_value_retained") is not False:
        raise ValueError("operation_causality_transport_raw_value_retained")
    if row.get("timestamp_window_attribution_used") is not False:
        raise ValueError("operation_causality_transport_timestamp_window_forbidden")
    if not all(
        _text(row.get(key))
        for key in (
            "assertion_id",
            "causal_scope_fingerprint",
            "operation_ref",
            "treatment_step_id",
            "value_source",
            "campaign_id",
            "execution_id",
        )
    ):
        raise ValueError("operation_causality_transport_identity_missing")
    return row


__all__ = [
    "TRANSPORT_RECEIPT_SCHEMA",
    "seal_operation_causality_transport_receipt",
    "validate_operation_causality_transport_receipt",
]
