"""Unit tests for the runtime undocumented-body field probe.

Locks in: source-declared scalar fields are probed with a scalar shape while
arrays/generic nouns use the array shape; the first 2xx candidate wins; an
exhausted probe returns an empty accepted_field receipt (never a fabricated
one); the ordering check only fires for source-declared FIFO/FEFO rules.
Synthetic behavior IR only — no benchmark material.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center import runtime_body_field_probe as _probe


def _behavior_ir() -> dict:
    return {
        "operations": [
            {
                "id": "op_a",
                "request_schema": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "properties": {
                                    "candidates": {"type": "array"},
                                    "status": {"type": "string"},
                                }
                            }
                        }
                    }
                },
            }
        ]
    }


def test_scalar_candidate_sent_with_scalar_shape(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_call(base_url, path, token, body, timeout=8.0):
        calls.append(body)
        return 422, {"detail": "invalid or incomplete business input"}

    monkeypatch.setattr(_probe, "_call_json", fake_call)
    result = _probe.probe_undocumented_request_fields(
        base_url="http://127.0.0.1:8120",
        path="/api/v1/wms/inventory/whatever/check",
        token="t",
        behavior_ir=_behavior_ir(),
        max_fields=4,
    )
    bodies = [body for body in calls]
    assert bodies[0] == {"candidates": [{"id": 1, "ts": 1}]}  # array shape
    assert bodies[1] == {"status": 1}  # scalar shape
    assert result["accepted_field"] == ""
    assert result["attempts"] == 4


def test_first_accepted_field_wins_and_returns_body(monkeypatch) -> None:
    def fake_call(base_url, path, token, body, timeout=8.0):
        if list(body) == ["status"]:
            return 200, {"result": True}
        return 422, {"detail": "invalid or incomplete business input"}

    monkeypatch.setattr(_probe, "_call_json", fake_call)
    result = _probe.probe_undocumented_request_fields(
        base_url="http://127.0.0.1:8120",
        path="/api/v1/wms/inventory/whatever/check",
        token="t",
        behavior_ir=_behavior_ir(),
    )
    assert result["accepted_field"] == "status"
    assert result["accepted_body"] == {"status": 1}
    assert result["response_status"] == 200


def test_exhausted_probe_never_fabricates_a_field(monkeypatch) -> None:
    def fake_call(base_url, path, token, body, timeout=8.0):
        return 422, {"detail": "invalid or incomplete business input"}

    monkeypatch.setattr(_probe, "_call_json", fake_call)
    result = _probe.probe_undocumented_request_fields(
        base_url="http://127.0.0.1:8120",
        path="/api/v1/wms/inventory/whatever/check",
        token="t",
        behavior_ir={},
        max_fields=3,
    )
    assert result["probed"] is True
    assert result["accepted_field"] == ""
    assert result["accepted_body"] is None
    assert result["response_status"] == 0
    assert len(result["receipts"]) == 3


def test_ordering_check_skipped_without_root_project(monkeypatch, tmp_path) -> None:
    def fake_call(base_url, path, token, body, timeout=8.0):
        return 200, {"result": "x"}

    monkeypatch.setattr(_probe, "_call_json", fake_call)
    result = _probe.probe_undocumented_request_fields(
        base_url="http://127.0.0.1:8120",
        path="/api/v1/wms/inventory/whatever/check",
        token="t",
        behavior_ir=_behavior_ir(),
        root=None,
        project="",
    )
    assert "ordering_check" not in result
