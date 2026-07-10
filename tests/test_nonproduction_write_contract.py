from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from ai_test_asset_center.sandbox_write_executor import execute_with_sandbox_write


def _scenario() -> SimpleNamespace:
    return SimpleNamespace(
        id="source-derived-write",
        actor_token="test-token",
        actor_role="tester",
        behavior_slice_id="slice-source-derived",
        execution_policy="approved_sandbox_write",
        steps=[SimpleNamespace(api_method="POST", api_path="/source-derived-resources", body_template={"value": 1})],
    )


def _runtime(environment: str, mode: str = "approved_sandbox_write") -> dict[str, str]:
    return {
        "status": "approved",
        "approved_base_url": "https://target.invalid",
        "environment_ref": environment,
        "environment_kind": environment,
        "execution_mode": mode,
    }


def test_direct_scan_defaults_declared_nonproduction_to_governed_write() -> None:
    from ai_test_asset_center.__main__ import _apply_scan_execution_defaults

    context = _apply_scan_execution_defaults(
        {"scope_id": "checkout", "environment_ref": "customer-qa"},
        "https://qa.example.test",
    )

    assert context["execution_mode"] == "approved_sandbox_write"
    assert context["test_data_contract"] == {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": "checkout",
    }


def test_direct_scan_preserves_read_only_kill_switch() -> None:
    from ai_test_asset_center.__main__ import _apply_scan_execution_defaults

    context = _apply_scan_execution_defaults(
        {
            "scope_id": "checkout",
            "environment_ref": "customer-qa",
            "execution_mode": "safe_read_only",
        },
        "https://qa.example.test",
    )

    assert context["execution_mode"] == "safe_read_only"
    assert "test_data_contract" not in context


def test_direct_scan_keeps_production_read_only() -> None:
    from ai_test_asset_center.__main__ import _apply_scan_execution_defaults

    context = _apply_scan_execution_defaults(
        {"scope_id": "checkout", "environment_ref": "customer-production"},
        "https://prod.example.test",
    )

    assert context["execution_mode"] == "safe_read_only"
    assert "test_data_contract" not in context


def test_direct_scan_keeps_unknown_environment_read_only() -> None:
    from ai_test_asset_center.__main__ import _apply_scan_execution_defaults

    context = _apply_scan_execution_defaults(
        {"scope_id": "checkout", "environment_ref": "customer-primary"},
        "https://customer.example.test",
    )

    assert context["execution_mode"] == "safe_read_only"
    assert "test_data_contract" not in context


def test_pilot_runtime_policy_matches_environment_governed_execution_contract() -> None:
    from ai_test_asset_center.enterprise_pilot_runtime import _default_config

    policies = _default_config("project")["policies"]

    assert policies["default_execution_mode"] == "environment_governed"
    assert policies["declared_nonproduction_execution_mode"] == "approved_sandbox_write"
    assert policies["production_execution_mode"] == "safe_read_only"
    assert policies["production_write_blocked"] is True
    assert policies["unknown_environment_write_blocked"] is True
    assert policies["independent_approval_required"] is False


def test_production_write_is_blocked_before_executor_is_called(tmp_path: Path) -> None:
    calls: list[str] = []

    result = execute_with_sandbox_write(
        _scenario(),
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("production"),
        execute_fn=lambda *args, **kwargs: calls.append("called") or {"steps": []},
    )

    assert calls == []
    assert result["sandbox_write"]["status"] == "blocked"
    assert result["steps"][0]["status"] == 0
    assert "production_environment_blocked" in result["sandbox_write"]["reason"]


def test_unknown_environment_write_is_fail_closed(tmp_path: Path) -> None:
    calls: list[str] = []

    result = execute_with_sandbox_write(
        _scenario(),
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("customer-primary"),
        execute_fn=lambda *args, **kwargs: calls.append("called") or {"steps": []},
    )

    assert calls == []
    assert result["sandbox_write"]["status"] == "blocked"
    assert result["sandbox_write"]["reason"].startswith("environment_not_recognized_nonprod")


def test_explicit_read_only_mode_blocks_write_before_executor(tmp_path: Path) -> None:
    calls: list[str] = []

    result = execute_with_sandbox_write(
        _scenario(),
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("staging", "safe_read_only"),
        execute_fn=lambda *args, **kwargs: calls.append("called") or {"steps": []},
    )

    assert calls == []
    assert result["sandbox_write"]["reason"] == "execution_mode_read_only"


def test_nonproduction_write_executes_once_and_records_cleanup(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._http_request",
        lambda method, url, **kwargs: {"status": 200, "body": {}, "headers": {}},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._cleanup_after_write",
        lambda **kwargs: {"status": "completed", "receipt_ref": "/source-derived-resources/id"},
    )

    def execute(*args, **kwargs):
        calls.append("called")
        return {
            "steps": [{
                "method": "POST",
                "path": "/source-derived-resources",
                "status": 201,
                "response": {"status_code": 201, "body": {"id": "created-id"}},
            }],
            "errors": [],
        }

    result = execute_with_sandbox_write(
        _scenario(),
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("pre-release"),
        campaign_id="campaign-1",
        execute_fn=execute,
    )

    assert calls == ["called"]
    assert result["sandbox_write"]["status"] == "completed"
    assert result["sandbox_write"]["cleanup"]["status"] == "completed"
    assert Path(result["sandbox_write"]["audit_path"]).exists()


def test_created_resource_identity_uses_source_path_and_generic_id_suffix() -> None:
    from ai_test_asset_center.sandbox_write_executor import _extract_resource_id

    assert _extract_resource_id({"refundId": "rf-123", "orderId": "ord-9"}, "/api/refunds") == "rf-123"
    assert _extract_resource_id({"data": {"case_id": "case-7"}}, "/api/cases") == "case-7"
    assert _extract_resource_id({"leftId": "a", "rightId": "b"}, "/api/links") == ""


def test_sandbox_observer_uses_source_declared_read_path_only() -> None:
    from ai_test_asset_center.sandbox_write_executor import _documented_observation_path

    scenario = SimpleNamespace(
        steps=[
            SimpleNamespace(action="resolve_refund", api_method="GET", api_path="/api/refunds"),
            SimpleNamespace(action="approve", api_method="POST", api_path="/api/refunds/{id}/approve"),
        ]
    )

    assert _documented_observation_path(
        scenario,
        "/api/refunds/{id}/approve",
        [{"method": "GET", "path": "/api/refunds/{id}"}],
    ) == "/api/refunds"
    assert _documented_observation_path(
        SimpleNamespace(steps=[SimpleNamespace(action="write", api_method="POST", api_path="/api/actions/run")]),
        "/api/actions/run",
        [{"method": "POST", "path": "/api/actions/run"}],
    ) == ""


def test_post_cleanup_requires_documented_delete_route(monkeypatch) -> None:
    from ai_test_asset_center.sandbox_write_executor import _cleanup_after_write

    calls: list[tuple[str, str]] = []

    def request(method: str, url: str, **kwargs):
        calls.append((method, url))
        return {"status": 204, "body": {}, "headers": {}}

    monkeypatch.setattr("ai_test_asset_center.sandbox_write_executor._http_request", request)

    blocked = _cleanup_after_write(
        method="POST",
        path="/api/refunds",
        base_url="https://target.invalid",
        token="test-token",
        before_body={},
        write_body={"refundId": "rf-123"},
        documented_routes=[{"method": "POST", "path": "/api/refunds"}],
    )
    assert blocked["status"] == "failed"
    assert blocked["error"] == "documented_cleanup_route_missing"
    assert calls == []

    completed = _cleanup_after_write(
        method="POST",
        path="/api/refunds",
        base_url="https://target.invalid",
        token="test-token",
        before_body={},
        write_body={"refundId": "rf-123"},
        documented_routes=[{"method": "DELETE", "path": "/api/refunds/{refundId}"}],
    )
    assert completed["status"] == "completed"
    assert completed["receipt_ref"] == "/api/refunds/rf-123"
    assert calls == [("DELETE", "https://target.invalid/api/refunds/rf-123")]


def test_native_login_write_uses_governed_observer_token_for_snapshots_and_cleanup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    observed_tokens: list[str] = []
    cleanup_calls: list[dict[str, object]] = []

    def observe(method: str, url: str, **kwargs):
        observed_tokens.append(str(kwargs.get("token") or ""))
        return {"status": 200, "body": {}, "headers": {}}

    def cleanup(**kwargs):
        cleanup_calls.append(dict(kwargs))
        return {"status": "completed", "receipt_ref": "/api/resources/created-id"}

    monkeypatch.setattr("ai_test_asset_center.sandbox_write_executor._http_request", observe)
    monkeypatch.setattr("ai_test_asset_center.sandbox_write_executor._cleanup_after_write", cleanup)

    scenario = SimpleNamespace(
        id="native-login-write",
        actor_token="",
        actor_role="buyer",
        behavior_slice_id="slice-native-login",
        execution_policy="approved_sandbox_write",
        steps=[
            SimpleNamespace(action="login", api_method="POST", api_path="/api/auth/login", body_template={"user": "buyer"}),
            SimpleNamespace(action="create", api_method="POST", api_path="/api/resources", body_template={"value": 1}),
        ],
    )

    result = execute_with_sandbox_write(
        scenario,
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("qa"),
        observer_token="governed-observer-token",
        documented_routes=[
            {"method": "GET", "path": "/api/resources"},
            {"method": "POST", "path": "/api/resources"},
        ],
        execute_fn=lambda *args, **kwargs: {
            "steps": [
                {
                    "action": "login",
                    "method": "POST",
                    "path": "/api/auth/login",
                    "status": 200,
                    "response": {"status_code": 200, "body": {"token": "native-token"}},
                },
                {
                    "action": "create",
                    "method": "POST",
                    "path": "/api/resources",
                    "status": 201,
                    "response": {"status_code": 201, "body": {"resourceId": "created-id"}},
                },
            ],
            "errors": [],
        },
    )

    assert result["sandbox_write"]["status"] == "completed"
    assert observed_tokens == ["governed-observer-token", "governed-observer-token"]
    assert [str(call.get("token") or "") for call in cleanup_calls] == ["governed-observer-token"]
    assert cleanup_calls[0]["path"] == "/api/resources"
    assert cleanup_calls[0]["write_body"] == {"resourceId": "created-id"}
    assert scenario.actor_token == ""

    from ai_test_asset_center.v12_pipeline import _summarize_execution_skip_telemetry

    telemetry = _summarize_execution_skip_telemetry([(scenario, result)])
    assert telemetry["sandbox_write_status_counts"] == {"completed": 1}
    assert telemetry["cleanup_status_counts"] == {"completed": 1}
    assert telemetry["observer_status_counts"] == {"after:200": 1, "before:200": 1}
