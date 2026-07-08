from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.regression_runner import _execute_http_probe


class _FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"{}") -> None:
        self.status = status
        self._body = body

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_execute_http_probe_uses_probe_request_body(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_urlopen(request, timeout: float = 0.0):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = request.data
        captured["method"] = request.get_method()
        captured["content_type"] = request.get_header("Content-type")
        return _FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    result = _execute_http_probe(
        probe={
            "method": "POST",
            "path": "/api/coupons/validate",
            "request_body": {"code": "NEW100", "totalAmount": 99.0},
        },
        cfg={"base_url": "http://localhost:8080"},
        project="enterprise-project",
        root=Path("."),
        timeout=5.0,
    )

    assert result["reachable"] is True
    assert captured["url"] == "http://localhost:8080/api/coupons/validate"
    assert captured["method"] == "POST"
    assert json.loads(captured["body"].decode("utf-8")) == {"code": "NEW100", "totalAmount": 99.0}
    assert captured["content_type"] == "application/json"

