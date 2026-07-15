from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.sandbox_write_executor import (
    _cleanup_after_write,
    execute_governed_control_write,
)
from ai_test_asset_center.sandbox_write_executor_base import (
    _materialize_source_observed_mutation,
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


def test_governed_write_materializes_source_observed_boolean_mutation(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[tuple[str, str, object]] = []
    state = {"id": "resource-1", "selected": False, "qty": 1}

    def fake_request(method: str, url: str, **kwargs):
        nonlocal state
        calls.append((method, url, kwargs.get("body")))
        if method == "PATCH":
            state = {**state, **dict(kwargs["body"])}
            return {"status": 200, "body": dict(state), "headers": {}}
        return {"status": 200, "body": [dict(state)], "headers": {}}

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
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-runtime-mutation",
        operation_phase="experiment_control",
        actor_identity="owner",
        actor_token="token",
        method="PATCH",
        path="/resources/resource-1",
        body=None,
        observation_path="/resources",
        runtime_body_plan={
            "schema_version": "qualibug.source-observed-mutation-plan.v1",
            "candidate_fields": ["qty", "selected"],
            "identity_bindings": {"id": "resource-1"},
        },
    )

    assert receipt["accepted"] is True
    assert receipt["materialized_request_body"] == {"selected": True}
    assert receipt["runtime_body_receipt"]["status"] == "MATERIALIZED"
    assert receipt["runtime_body_receipt"]["selected_field"] == "selected"
    assert "selected_value" not in receipt["runtime_body_receipt"]
    assert calls == [
        ("GET", "http://target.invalid/resources", None),
        ("PATCH", "http://target.invalid/resources/resource-1", {"selected": True}),
        ("GET", "http://target.invalid/resources", None),
    ]


def test_governed_write_blocks_ambiguous_runtime_mutation_before_transport(
    monkeypatch,
    tmp_path,
) -> None:
    calls: list[str] = []

    def fake_request(method: str, url: str, **kwargs):
        calls.append(method)
        return {
            "status": 200,
            "body": [
                {"id": "resource-1", "selected": False},
                {"id": "resource-2", "selected": True},
            ],
            "headers": {},
        }

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
        base_url="http://target.invalid",
        runtime_contract={"environment_type": "test"},
        campaign_id="campaign-runtime-mutation-blocked",
        operation_phase="experiment_control",
        actor_identity="owner",
        actor_token="token",
        method="PATCH",
        path="/resources/resource-1",
        body=None,
        observation_path="/resources",
        runtime_body_plan={
            "schema_version": "qualibug.source-observed-mutation-plan.v1",
            "candidate_fields": ["selected"],
            "identity_bindings": {},
        },
    )

    assert receipt["accepted"] is False
    assert receipt["reason"] == "runtime_mutation_target_ambiguous"
    assert receipt["write"]["status"] == 0
    assert receipt["runtime_body_receipt"]["status"] == "BLOCKED"
    assert calls == ["GET"]


def test_source_observed_mutation_preserves_decimal_string_shape() -> None:
    body, receipt, reason = _materialize_source_observed_mutation(
        {"sku": "SKU-1", "original_price": "12.50", "price": "10.50"},
        {
            "schema_version": "qualibug.source-observed-mutation-plan.v1",
            "candidate_fields": ["original_price", "price"],
            "identity_bindings": {"sku": "SKU-1"},
        },
    )

    assert reason == ""
    assert body == {"price": "11.50"}
    assert receipt["value_type"] == "str_decimal"
