"""P0-A: typed receipts for the Chinese Semantic Frame pipeline.

Covers content-addressed receipt identity and fail-closed validation
(SPEC §5 receipts, §16 reason codes).
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_receipts import (
    CHINESE_SEMANTIC_RECEIPT_SCHEMA,
    RECEIPT_KINDS,
    build_receipt,
    validate_receipt,
)


def test_receipt_is_content_addressed() -> None:
    first = build_receipt(
        receipt_kind="FACT_PROJECTION",
        frame_id="csf:1",
        reason_codes=["TECHNICAL_GROUNDING_PENDING"],
        payload={"fact_id": "fact:1"},
    )
    second = build_receipt(
        receipt_kind="FACT_PROJECTION",
        frame_id="csf:1",
        reason_codes=["TECHNICAL_GROUNDING_PENDING"],
        payload={"fact_id": "fact:1"},
    )
    assert first["receipt_id"] == second["receipt_id"]
    assert first["receipt_id"].startswith("csf_")

    changed = build_receipt(
        receipt_kind="FACT_PROJECTION",
        frame_id="csf:1",
        reason_codes=["TECHNICAL_GROUNDING_PENDING"],
        payload={"fact_id": "fact:2"},
    )
    assert changed["receipt_id"] != first["receipt_id"]


def test_validate_receipt_accepts_valid() -> None:
    receipt = build_receipt(
        receipt_kind="BEHAVIOR_IR_PROJECTION",
        frame_id="",
        status="PARTIAL",
        reason_codes=["TECHNICAL_GROUNDING_PENDING"],
        payload={"frames_considered": 1},
    )
    assert validate_receipt(receipt) == []


def test_validate_receipt_rejects_tampered_content() -> None:
    receipt = build_receipt(
        receipt_kind="FACT_PROJECTION",
        frame_id="csf:1",
        payload={"fact_id": "fact:1"},
    )
    receipt["status"] = "FAIL"  # relabelling without recomputing the id
    errors = validate_receipt(receipt)
    assert "receipt_id_mismatch" in errors


def test_build_rejects_unknown_kind_and_status() -> None:
    with pytest.raises(ValueError, match="receipt_kind_invalid"):
        build_receipt(receipt_kind="MADE_UP_KIND")
    with pytest.raises(ValueError, match="receipt_status_invalid"):
        build_receipt(receipt_kind="FACT_PROJECTION", status="MAYBE")


def test_build_rejects_forbidden_terminal_reason_code() -> None:
    with pytest.raises(ValueError, match="forbidden_terminal_code"):
        build_receipt(receipt_kind="FACT_PROJECTION", reason_codes=["no_match"])


def test_receipt_kinds_are_closed() -> None:
    assert RECEIPT_KINDS == frozenset(
        {
            "FRAME_VALIDATION",
            "FACT_PROJECTION",
            "SIGNATURE_COMPUTED",
            "BEHAVIOR_IR_PROJECTION",
        }
    )
    assert CHINESE_SEMANTIC_RECEIPT_SCHEMA == "qualibug.chinese-semantic-receipt.v1"
