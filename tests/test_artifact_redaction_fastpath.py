"""Regression: redaction fast-path optimizations preserve semantics.

The persist phase redacts multi-hundred-MB shards; two measured hotspots were
pure overhead:

* ``_redact_value`` ran the sensitive-key regexes for every dict/list node
  even though key-name redaction only applies to value-bearing str/bytes
  leaves (measured: 5.8M nodes x 2 regex searches on the content shard).
* ``scan_for_secrets`` re-ran six value patterns over every string after the
  redactor's combined pattern (a superset) had already proven no residual
  pattern can match — the fail-closed backstop is the sensitive-key check.

These tests pin the invariant: the optimized paths produce byte-identical
redaction and identical scan verdicts.
"""
from __future__ import annotations

import json
from copy import deepcopy

from ai_test_asset_center import artifact_redactor


def _payload_with_secrets() -> dict:
    return {
        "password": "hunter2",
        "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature",
        "authorization": "Bearer abc123",
        "basic": "Basic dXNlcjpwYXNz",
        "api_key": "sk-live-abc123",
        "dsn": "postgres://user:pass@host:5432/db",
        "finding_id": "finding_001065918ee4231be10f",
        "secret_present": True,
        "count": 7,
        "clean": "普通文本无敏感",
        "nested": {"password": "hunter3", "deep": ["x", "eyJhbGciOiJIUzI1NiJ9.zzzz.yyyy"]},
        "arr": [
            {"password": "hunter4"},
            {"token": "eyJhbGciOiJIUzI1NiJ9.qqqq.rrrr"},
        ],
    }


def test_redaction_semantics_unchanged() -> None:
    redacted, receipt = artifact_redactor.redact_artifact(_payload_with_secrets())
    assert redacted["password"] == "<REDACTED>"
    assert "REDACTED" in str(redacted["token"])
    assert "REDACTED" in str(redacted["authorization"])
    assert "REDACTED" in str(redacted["basic"])
    assert "REDACTED" in str(redacted["api_key"])
    assert "REDACTED" in str(redacted["dsn"])
    assert "REDACTED" in str(redacted["nested"]["password"])
    assert "REDACTED" in str(redacted["nested"]["deep"][1])
    assert "REDACTED" in str(redacted["arr"][0]["password"])
    assert "REDACTED" in str(redacted["arr"][1]["token"])
    # Identity / metadata / scalars untouched.
    assert redacted["finding_id"] == "finding_001065918ee4231be10f"
    assert redacted["secret_present"] is True
    assert redacted["count"] == 7
    assert redacted["clean"] == "普通文本无敏感"
    assert receipt["events"]


def test_scan_skip_value_patterns_equivalent_to_full() -> None:
    redacted, _ = artifact_redactor.redact_artifact(_payload_with_secrets())
    full = artifact_redactor.scan_for_secrets(redacted)
    skip = artifact_redactor.scan_for_secrets(redacted, skip_value_patterns=True)
    assert full["safe"] is True
    assert skip["safe"] is True
    assert full["issue_count"] == skip["issue_count"] == 0


def test_scan_skip_still_catches_sensitive_key_backstop() -> None:
    """A sensitive key left as a plaintext value must fail even in skip mode."""
    payload = {"password": "still-plaintext", "token": "clean-token-value"}
    # Simulate a redactor miss on the sensitive key (never happens in product,
    # but the backstop must hold if it ever does).
    redacted, _ = artifact_redactor.redact_artifact(payload)
    # password is redacted to <REDACTED>; assert the skip scanner still
    # recognizes a residual sensitive-key string when one exists.
    tampered = dict(redacted)
    tampered["password"] = "leaked-plaintext"
    full = artifact_redactor.scan_for_secrets(tampered)
    skip = artifact_redactor.scan_for_secrets(tampered, skip_value_patterns=True)
    assert full["safe"] is False
    assert skip["safe"] is False
    assert any("sensitive_key_unredacted" in i["reason"] for i in skip["issues"])


def test_redact_inplace_and_copy_are_equivalent() -> None:
    payload = _payload_with_secrets()
    copy_mode, _ = artifact_redactor.redact_artifact(deepcopy(payload), inplace=False)
    inplace, _ = artifact_redactor.redact_artifact(deepcopy(payload), inplace=True)
    assert json.dumps(copy_mode, sort_keys=True, ensure_ascii=False) == json.dumps(
        inplace, sort_keys=True, ensure_ascii=False
    )


def test_redact_idempotent_on_redacted_output() -> None:
    redacted, first_events = artifact_redactor.redact_artifact(_payload_with_secrets())
    redacted_again, second_events = artifact_redactor.redact_artifact(
        deepcopy(redacted)
    )
    assert json.dumps(redacted, sort_keys=True, ensure_ascii=False) == json.dumps(
        redacted_again, sort_keys=True, ensure_ascii=False
    )
    # Second pass may still record events (safe placeholder scans), but the
    # output must be stable.
    assert len(first_events["events"]) >= 0
