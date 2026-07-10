from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.sandbox_write_executor import (
    _cleanup_after_write,
    execute_governed_control_write,
)


def test_cleanup_retry_count_changes_real_cleanup_attempts(monkeypatch) -> None:
    calls: list[int] = []

    def request(method: str, url: str, **kwargs):
        calls.append(len(calls) + 1)
        status = 503 if len(calls) < 3 else 204
        return {"status": status, "body": {}, "headers": {}}

    monkeypatch.setattr("ai_test_asset_center.sandbox_write_executor._http_request", request)
    result = _cleanup_after_write(
        method="POST",
        path="/api/resources",
        base_url="https://target.invalid",
        token="test-token",
        before_body={},
        write_body={"id": "resource-1"},
        documented_routes=[{"method": "DELETE", "path": "/api/resources/{id}"}],
        retry_count=2,
    )

    assert result["status"] == "completed"
    assert result["attempts"] == 3
    assert calls == [1, 2, 3]


def test_cleanup_retry_count_remains_bounded(monkeypatch) -> None:
    calls: list[int] = []

    def request(method: str, url: str, **kwargs):
        calls.append(len(calls) + 1)
        return {"status": 503, "body": {}, "headers": {}}

    monkeypatch.setattr("ai_test_asset_center.sandbox_write_executor._http_request", request)
    result = _cleanup_after_write(
        method="PATCH",
        path="/api/resources/resource-1",
        base_url="https://target.invalid",
        token="test-token",
        before_body={"status": "before"},
        write_body={"status": "after"},
        retry_count=99,
    )

    assert result["status"] == "failed"
    assert result["attempts"] == 4
    assert len(calls) == 4


def test_governed_control_write_emits_real_before_after_and_audit(monkeypatch, tmp_path) -> None:
    calls: list[tuple[str, str]] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append((method, url))
        return {"status": 200, "body": {"ok": True}, "headers": {}, "duration_ms": 1}

    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor.sandbox_write_allowed",
        lambda **_: (True, "approved"),
    )
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._http_request",
        fake_request,
    )

    receipt = execute_governed_control_write(
        root=tmp_path,
        project="evaluation-project",
        base_url="http://127.0.0.1:8011",
        runtime_contract={
            "status": "approved",
            "execution_mode": "approved_sandbox_write",
            "approved_base_url": "http://127.0.0.1:8011",
            "environment_kind": "sandbox",
        },
        campaign_id="campaign-1",
        operation_phase="evaluation_fixture_cleanup",
        actor_identity="evaluation-fixture-controller",
        actor_token="",
        method="POST",
        path="/__reset",
        body={},
        observation_path="/__state",
    )

    assert receipt["accepted"] is True
    assert calls == [
        ("GET", "http://127.0.0.1:8011/__state"),
        ("POST", "http://127.0.0.1:8011/__reset"),
        ("GET", "http://127.0.0.1:8011/__state"),
    ]
    assert Path(receipt["audit_path"]).is_file()
