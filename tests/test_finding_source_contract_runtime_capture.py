# -*- coding: utf-8 -*-
"""Unit tests: observed request/response material into finding runtime evidence.

Covers the task-6 mechanism in ``finding_source_contract.py``:
- executed query parameter shapes (key names verbatim, instance UUIDs stripped,
  real short literals — including negative numbers — kept verbatim),
- request bodies that actually reached transport,
- response bodies the target returned (redacted + truncated),
- red line: no fabricated values (blocked steps never count as observations)
  and no secret plaintext (password/token redacted via artifact_redactor).
"""
from __future__ import annotations

import json

from ai_test_asset_center.finding_source_contract import (
    attach_evidence_paragraphs,
    build_observed_request_text,
    build_observed_response_text,
    build_runtime_evidence_text,
    normalize_observed_value,
    query_shape_tokens,
)


# ── value-shape normalization ──────────────────────────────────────────────
def test_instance_uuids_normalize_to_shape_token():
    assert normalize_observed_value("9e8bccfb-cab5-4584-89a0-9dc79a922412") == "<uuid>"
    assert normalize_observed_value("0b0a3eb4e7d84f5b999040332c529880") == "<uuid>"


def test_datetime_and_generated_ids_normalize():
    assert normalize_observed_value("2026-08-08T09:53:38.598Z") == "<datetime>"
    assert normalize_observed_value("BM17861828185982886") == "<id>"


def test_real_short_literals_kept_verbatim():
    # Real observed values are kept as-is — never guessed, never templated.
    assert normalize_observed_value("-999") == "-999"
    assert normalize_observed_value("2") == "2"
    assert normalize_observed_value("6999.00") == "6999.00"
    assert normalize_observed_value("SKU-PHONE-001") == "SKU-PHONE-001"
    assert normalize_observed_value("ACTIVE") == "ACTIVE"


# ── query shape extraction ─────────────────────────────────────────────────
def test_query_shape_tokens_keys_verbatim_values_normalized():
    tokens = query_shape_tokens(
        "/api/users/addresses?userId=e75dd603-df15-4225-9632-5b4cf2511022"
        "&page=2&amount=-999&status=ACTIVE&empty="
    )
    assert tokens == ["userId=<uuid>", "page=2", "amount=-999", "status=ACTIVE", "empty"]


def test_query_shape_no_query_returns_empty():
    assert query_shape_tokens("/api/orders") == []
    assert query_shape_tokens(None) == []


# ── redaction: no secret plaintext in evidence material ────────────────────
def _finding_with_step(body, *, request_body=None, status_code=200, path="/api/x"):
    steps = [{
        "status_code": status_code,
        "path": path,
        "body": body,
        "governance_receipt": (
            {"materialized_request_body": request_body}
            if request_body is not None else {}
        ),
    }]
    return {
        "raw_evidence": {
            "steps": steps,
            "request_raw": {"path": path},
            "response_raw": {"status_code": status_code, "body": body},
        },
        "reproduction": {"actor": "buyer", "method": "GET", "path": path},
    }


def test_sensitive_fields_redacted_in_response_text():
    finding = _finding_with_step({
        "id": "00000000-0000-0000-0000-000000000001",
        "user_id": "e75dd603-df15-4225-9632-5b4cf2511022",
        "sku": "SKU-PHONE-001",
        "token": "SECRETTOKENVALUE",
        "password": "hunter2",
        "status": "ACTIVE",
    })
    text = build_observed_response_text(finding)
    assert "user_id=<uuid>" in text
    assert "SKU-PHONE-001" in text
    assert "<REDACTED>" in text
    assert "SECRETTOKENVALUE" not in text
    assert "hunter2" not in text


def test_sensitive_fields_redacted_in_request_text():
    finding = _finding_with_step(
        {"ok": True},
        request_body={"userId": "u-1", "new_password": "p@ss", "amount": -999},
    )
    text = build_observed_request_text(finding)
    assert "userId=u-1" in text
    assert "amount=-999" in text
    assert "p@ss" not in text
    assert "<REDACTED>" in text


# ── truncation ─────────────────────────────────────────────────────────────
def test_response_body_truncated_and_list_summarized():
    big_body = {"items": [{"a": i} for i in range(50)], "note": "x" * 5000}
    finding = _finding_with_step(big_body)
    text = build_observed_response_text(finding, limit=300)
    assert len(text) <= 300
    assert "N=" in text  # list summarized instead of exploded


# ── red line: only real transport observations count ───────────────────────
def test_blocked_steps_are_not_observations():
    finding = {
        "raw_evidence": {
            "steps": [
                {"status_code": 0, "path": "/api/x?uid=11111111-1111-1111-1111-111111111111",
                 "body": {"user_id": "e75dd603-df15-4225-9632-5b4cf2511022"},
                 "governance_receipt": {"materialized_request_body": {"amount": -999}}},
            ],
            "request_raw": {"path": "/api/x"},
            "response_raw": {"status_code": 0, "body": {}},
        },
        "reproduction": {"actor": "buyer", "method": "GET", "path": "/api/x"},
    }
    # A pre-transport blocked step produced no observation: no query shape,
    # no request body, no response body may be claimed.
    assert build_observed_request_text(finding) == ""
    assert build_observed_response_text(finding) == ""
    evidence = build_runtime_evidence_text(finding)
    assert "观察query形态" not in evidence
    assert "观察请求体" not in evidence
    assert "观察响应体" not in evidence


# ── evidence paragraph integration ─────────────────────────────────────────
def test_runtime_evidence_includes_observed_payload_segments():
    finding = _finding_with_step(
        {"sku": "SKU-PHONE-001", "qty": 1, "price": "6999.00", "user_id": "u-9"},
        path="/api/cart/items?userId=e75dd603-df15-4225-9632-5b4cf2511022",
    )
    evidence = build_runtime_evidence_text(finding)
    assert "观察query形态=userId=<uuid>" in evidence
    assert "观察响应体=" in evidence
    assert "SKU-PHONE-001" in evidence
    assert "qty=1" in evidence


def test_legacy_mode_excludes_payload_segments():
    finding = _finding_with_step(
        {"sku": "SKU-PHONE-001", "qty": 1},
        path="/api/cart/items?userId=e75dd603-df15-4225-9632-5b4cf2511022",
    )
    evidence = build_runtime_evidence_text(finding, include_observed_payloads=False)
    assert "观察query形态" not in evidence
    # Legacy path may keep the JSON body summary, but never the flattened
    # key=value material (no "sku=SKU-PHONE-001" token form).
    assert "sku=SKU-PHONE-001" not in evidence


def test_attach_evidence_paragraphs_passthrough_and_idempotent():
    finding = _finding_with_step(
        {"sku": "SKU-PHONE-001", "qty": 1},
        path="/api/cart/items?userId=e75dd603-df15-4225-9632-5b4cf2511022",
    )
    finding["description"] = "original"
    enriched = attach_evidence_paragraphs(finding, statements=["规则A"])
    description = enriched["description"]
    assert "源契约: 规则A" in description
    assert "运行时证据: " in description
    assert "观察query形态=userId=<uuid>" in description
    # Idempotent: a second pass must not duplicate paragraphs.
    enriched2 = attach_evidence_paragraphs(enriched, statements=["规则A"])
    assert enriched2["description"].count("运行时证据: ") == 1
    assert enriched2["description"].count("源契约: ") == 1
    assert "original" in enriched2["description"]
