"""Typed receipts for the Chinese Semantic Frame pipeline (SPEC §5, §16).

P0-A contract:
- Every frame lifecycle step (validation, fact projection, signature
  computation, Behavior IR projection) emits a typed receipt with a
  content-addressed ``receipt_id`` (``csf_`` + sha256 over the canonical
  receipt content) so a receipt can never be re-labelled or re-attributed
  without changing its identity.
- Validation is fail-closed: an invalid receipt reports errors and is never
  accepted by a consumer.
- Reason codes reuse the frame vocabulary from ``chinese_semantic_schema``;
  forbidden terminal codes are rejected.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from .chinese_semantic_schema import (
    FORBIDDEN_TERMINAL_REASON_CODES,
    REASON_CODES,
    _canonical_json,
    _dict,
    _list,
    _text,
)

CHINESE_SEMANTIC_RECEIPT_SCHEMA = "qualibug.chinese-semantic-receipt.v1"

RECEIPT_KINDS = frozenset(
    {
        "FRAME_VALIDATION",
        "FACT_PROJECTION",
        "SIGNATURE_COMPUTED",
        "BEHAVIOR_IR_PROJECTION",
    }
)

RECEIPT_STATUSES = frozenset({"PASS", "PARTIAL", "FAIL"})

_RECEIPT_ID_PREFIX = "csf_"


def _receipt_id(
    *,
    receipt_kind: str,
    frame_id: str,
    status: str,
    reason_codes: Iterable[str],
    payload: dict[str, Any],
) -> str:
    content = {
        "schema": CHINESE_SEMANTIC_RECEIPT_SCHEMA,
        "receipt_kind": receipt_kind,
        "frame_id": frame_id,
        "status": status,
        "reason_codes": sorted(set(_text(code) for code in reason_codes)),
        "payload": payload,
    }
    encoded = _canonical_json(content)
    return _RECEIPT_ID_PREFIX + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def build_receipt(
    *,
    receipt_kind: str,
    frame_id: str = "",
    status: str = "PASS",
    reason_codes: Iterable[str] = (),
    payload: dict[str, Any] | None = None,
    source_refs: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Build a content-addressed typed receipt for a frame pipeline step."""
    if receipt_kind not in RECEIPT_KINDS:
        raise ValueError(f"chinese_semantic_receipt_kind_invalid:{receipt_kind}")
    if status not in RECEIPT_STATUSES:
        raise ValueError(f"chinese_semantic_receipt_status_invalid:{status}")
    payload = dict(payload or {})
    codes = sorted(
        {_text(code) for code in reason_codes if _text(code)}
    )
    for code in codes:
        if code in FORBIDDEN_TERMINAL_REASON_CODES:
            raise ValueError(f"chinese_semantic_receipt_forbidden_terminal_code:{code}")
        if code not in REASON_CODES:
            raise ValueError(f"chinese_semantic_receipt_reason_code_invalid:{code}")
    return {
        "schema": CHINESE_SEMANTIC_RECEIPT_SCHEMA,
        "receipt_id": _receipt_id(
            receipt_kind=receipt_kind,
            frame_id=_text(frame_id),
            status=status,
            reason_codes=codes,
            payload=payload,
        ),
        "receipt_kind": receipt_kind,
        "frame_id": _text(frame_id),
        "status": status,
        "reason_codes": codes,
        "payload": dict(payload),
        "source_refs": [
            dict(row) for row in source_refs if isinstance(row, dict)
        ],
    }


def validate_receipt(receipt: dict[str, Any]) -> list[str]:
    """Return structural violations; an empty list means the receipt is valid."""
    errors: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt_not_object"]
    if _text(receipt.get("schema")) != CHINESE_SEMANTIC_RECEIPT_SCHEMA:
        errors.append("receipt_schema_mismatch")
    receipt_kind = _text(receipt.get("receipt_kind"))
    if receipt_kind not in RECEIPT_KINDS:
        errors.append(f"receipt_kind_invalid:{receipt_kind}")
    status = _text(receipt.get("status"))
    if status not in RECEIPT_STATUSES:
        errors.append(f"receipt_status_invalid:{status}")
    frame_id = _text(receipt.get("frame_id"))
    reason_codes = [ _text(code) for code in _list(receipt.get("reason_codes")) ]
    for code in reason_codes:
        if code not in REASON_CODES:
            errors.append(f"receipt_reason_code_invalid:{code}")
        if code in FORBIDDEN_TERMINAL_REASON_CODES:
            errors.append(f"receipt_forbidden_terminal_reason_code:{code}")
    payload = dict(_dict(receipt.get("payload")))
    expected_id = _receipt_id(
        receipt_kind=receipt_kind,
        frame_id=frame_id,
        status=status,
        reason_codes=reason_codes,
        payload=payload,
    )
    if _text(receipt.get("receipt_id")) != expected_id:
        errors.append("receipt_id_mismatch")
    return errors
