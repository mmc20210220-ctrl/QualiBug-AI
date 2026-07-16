from __future__ import annotations

from typing import Any

import pytest

from ai_test_asset_center.sandbox_write_executor_base import (
    _http_request,
    evaluator_request_trace,
)


class _Response:
    status = 200
    headers: dict[str, str] = {}

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self, _: int) -> bytes:
        return b'{"ok":true}'


def test_http_transport_emits_scoped_evaluator_correlation_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, str]] = []

    def _open(request: Any, *, timeout: float) -> _Response:
        assert timeout == 10.0
        captured.append({key.lower(): value for key, value in request.header_items()})
        return _Response()

    monkeypatch.setattr("urllib.request.urlopen", _open)
    trace = {
        "run_id": "RUN-1",
        "campaign_id": "CAMPAIGN-1",
        "target_id": "TARGET-1",
        "obligation_id": "OBLIGATION-1",
        "execution_id": "EXECUTION-1",
    }

    with evaluator_request_trace(trace):
        response = _http_request("GET", "http://example.test/items")
    _http_request("GET", "http://example.test/after")

    assert response["status"] == 200
    assert captured[0] | {} == {
        "accept": "application/json",
        "x-qualibug-run-id": "RUN-1",
        "x-qualibug-campaign-id": "CAMPAIGN-1",
        "x-qualibug-target-id": "TARGET-1",
        "x-qualibug-obligation-id": "OBLIGATION-1",
        "x-qualibug-execution-id": "EXECUTION-1",
    }
    assert set(captured[1]) == {"accept"}


def test_evaluator_request_trace_rejects_header_injection() -> None:
    with pytest.raises(ValueError, match="request_trace_value_invalid"):
        with evaluator_request_trace({
            "run_id": "RUN-1\r\nX-Forged: yes",
            "campaign_id": "CAMPAIGN-1",
            "target_id": "TARGET-1",
            "obligation_id": "OBLIGATION-1",
            "execution_id": "EXECUTION-1",
        }):
            pass
