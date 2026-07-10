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
    assert _documented_observation_path(
        SimpleNamespace(steps=[SimpleNamespace(action="write", api_method="POST", api_path="/api/refunds")]),
        "/api/refunds",
        [{"method": "GET", "path": "/api/refunds/{refundId}"}],
    ) == ""


def test_fixture_lifecycle_is_governed_and_audited(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.sandbox_write_executor import execute_governed_fixture_lifecycle

    callbacks: list[str] = []

    def observe(method: str, url: str, **kwargs):
        return {"status": 200, "body": {"records": []}, "headers": {}}

    monkeypatch.setattr("ai_test_asset_center.sandbox_write_executor._http_request", observe)
    runtime = _runtime("qa")
    result = execute_governed_fixture_lifecycle(
        root=tmp_path,
        project="project",
        base_url="https://target.invalid",
        runtime_contract=runtime,
        campaign_id="campaign-1",
        slice_id="slice-1",
        actor_identity="control",
        actor_token="token",
        observation_path="/api/resources",
        setup_execute_fn=lambda: (callbacks.append("setup") or [{"status": "executed", "accepted": True, "method": "POST", "path": "/api/resources"}]),
        cleanup_execute_fn=lambda: (callbacks.append("cleanup") or [{"status": "executed", "accepted": True, "method": "DELETE", "path": "/api/resources/id-1"}]),
    )

    assert result["status"] == "completed"
    assert callbacks == ["setup", "cleanup"]
    assert result["cleanup"]["status"] == "completed"
    assert len(result["audit_records"]) == 2
    audit = Path(result["audit_path"])
    assert audit.exists()
    lines = audit.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all('"campaign_id": "campaign-1"' in line for line in lines)


def test_fixture_lifecycle_kill_switch_blocks_before_callbacks(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.sandbox_write_executor import execute_governed_fixture_lifecycle

    callbacks: list[str] = []
    monkeypatch.setenv("QUALIBUG_DISABLE_SANDBOX_WRITE", "1")
    result = execute_governed_fixture_lifecycle(
        root=tmp_path,
        project="project",
        base_url="https://target.invalid",
        runtime_contract=_runtime("qa"),
        campaign_id="campaign-1",
        slice_id="slice-1",
        actor_identity="control",
        actor_token="token",
        observation_path="/api/resources",
        setup_execute_fn=lambda: (callbacks.append("setup") or []),
        cleanup_execute_fn=lambda: (callbacks.append("cleanup") or []),
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "write_probing_disabled_by_operator"
    assert callbacks == []


def test_fixture_lifecycle_cleans_up_partial_setup_attempt(tmp_path: Path, monkeypatch) -> None:
    from ai_test_asset_center.sandbox_write_executor import execute_governed_fixture_lifecycle

    callbacks: list[str] = []
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._http_request",
        lambda *args, **kwargs: {"status": 200, "body": {}, "headers": {}},
    )
    result = execute_governed_fixture_lifecycle(
        root=tmp_path,
        project="project",
        base_url="https://target.invalid",
        runtime_contract=_runtime("qa"),
        campaign_id="campaign-1",
        slice_id="slice-1",
        actor_identity="control",
        actor_token="token",
        observation_path="/api/resources",
        setup_execute_fn=lambda: (callbacks.append("setup") or [
            {"status": "executed", "accepted": True, "method": "POST", "path": "/api/resources"},
            {"status": "executed", "accepted": False, "method": "POST", "path": "/api/resources/dependency"},
        ]),
        cleanup_execute_fn=lambda: (callbacks.append("cleanup") or [
            {"status": "executed", "accepted": True, "method": "DELETE", "path": "/api/resources/id-1"},
        ]),
    )

    assert callbacks == ["setup", "cleanup"]
    assert result["status"] == "cleanup_incomplete"
    assert result["cleanup"]["status"] == "failed"


def test_fixture_lifecycle_does_not_send_cleanup_after_observed_unchanged_4xx_setup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from ai_test_asset_center.sandbox_write_executor import execute_governed_fixture_lifecycle

    callbacks: list[str] = []
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._http_request",
        lambda *args, **kwargs: {"status": 200, "body": {"records": []}, "headers": {}},
    )
    result = execute_governed_fixture_lifecycle(
        root=tmp_path,
        project="project",
        base_url="https://target.invalid",
        runtime_contract=_runtime("qa"),
        campaign_id="campaign-1",
        slice_id="slice-1",
        actor_identity="control",
        actor_token="token",
        observation_path="/api/resources",
        setup_execute_fn=lambda: (callbacks.append("setup") or [{
            "status": "executed",
            "accepted": False,
            "method": "POST",
            "path": "/api/resources",
            "response": {"status_code": 400},
        }]),
        cleanup_execute_fn=lambda: (callbacks.append("cleanup") or []),
    )

    assert callbacks == ["setup"]
    assert result["status"] == "cleanup_incomplete"
    assert result["cleanup"] == {
        "status": "not_required",
        "reason": "setup_rejected_observer_unchanged",
    }


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
    assert blocked["status"] == "not_reversible"
    assert blocked["warning"] == "documented_cleanup_route_missing"
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


def test_non_reversible_cleanup_never_reports_completed_lifecycle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._http_request",
        lambda *args, **kwargs: {"status": 200, "body": {}, "headers": {}},
    )
    scenario = _scenario()
    result = execute_with_sandbox_write(
        scenario,
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("qa"),
        documented_routes=[
            {"method": "GET", "path": "/source-derived-resources"},
            {"method": "POST", "path": "/source-derived-resources"},
        ],
        execute_fn=lambda *args, **kwargs: {
            "steps": [
                {
                    "method": "POST",
                    "path": "/source-derived-resources",
                    "status": 202,
                    "response": {"status_code": 202, "body": {"accepted": True}},
                }
            ],
            "errors": [],
        },
    )

    assert result["sandbox_write"]["cleanup"]["status"] == "not_reversible"
    assert result["sandbox_write"]["status"] == "cleanup_incomplete"


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


def test_multi_write_scenario_emits_one_governed_receipt_per_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._http_request",
        lambda *args, **kwargs: {"status": 200, "body": {}, "headers": {}},
    )
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._cleanup_after_write",
        lambda **kwargs: {"status": "completed", "receipt_ref": kwargs["path"]},
    )
    scenario = SimpleNamespace(
        id="multi-write",
        actor_token="test-token",
        actor_role="tester",
        behavior_slice_id="slice-multi-write",
        execution_policy="approved_sandbox_write",
        steps=[
            SimpleNamespace(
                action="bootstrap_create_resource",
                api_method="POST",
                api_path="/api/resources",
                body_template={"value": 1},
            ),
            SimpleNamespace(
                action="update_resource",
                api_method="PATCH",
                api_path="/api/resources/{id}",
                body_template={"value": 2},
            ),
        ],
    )

    def execute(*args, write_observer, **kwargs):
        first = write_observer(
            "before",
            {
                "action": "bootstrap_create_resource",
                "method": "POST",
                "path": "/api/resources",
                "body": {"value": 1},
                "token": "test-token",
            },
        )
        write_observer(
            "after",
            {
                "event_id": first,
                "action": "bootstrap_create_resource",
                "method": "POST",
                "path": "/api/resources",
                "status": 201,
                "response_body": {"id": "resource-1"},
                "token": "test-token",
            },
        )
        second = write_observer(
            "before",
            {
                "action": "update_resource",
                "method": "PATCH",
                "path": "/api/resources/resource-1",
                "body": {"value": 2},
                "token": "test-token",
            },
        )
        write_observer(
            "after",
            {
                "event_id": second,
                "action": "update_resource",
                "method": "PATCH",
                "path": "/api/resources/resource-1",
                "status": 200,
                "response_body": {"id": "resource-1", "value": 2},
                "token": "test-token",
            },
        )
        return {
            "steps": [
                {"action": "bootstrap_create_resource", "method": "POST", "path": "/api/resources", "status": 201},
                {"action": "update_resource", "method": "PATCH", "path": "/api/resources/resource-1", "status": 200},
            ],
            "errors": [],
        }

    result = execute_with_sandbox_write(
        scenario,
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("qa"),
        documented_routes=[
            {"method": "GET", "path": "/api/resources"},
            {"method": "POST", "path": "/api/resources"},
            {"method": "PATCH", "path": "/api/resources/{id}"},
            {"method": "DELETE", "path": "/api/resources/{id}"},
        ],
        execute_fn=execute,
    )

    assert result["sandbox_write"]["status"] == "completed"
    assert result["sandbox_write"]["governed_write_receipt_count"] == 2
    assert len(result["sandbox_write"]["audit_records"]) == 2
    assert len(Path(result["sandbox_write"]["audit_path"]).read_text(encoding="utf-8").splitlines()) == 2


def test_multi_write_scenario_without_per_write_hook_is_blocked_before_execution(tmp_path: Path) -> None:
    calls: list[str] = []
    scenario = SimpleNamespace(
        id="multi-write-no-hook",
        actor_token="test-token",
        actor_role="tester",
        behavior_slice_id="slice-multi-write-no-hook",
        execution_policy="approved_sandbox_write",
        steps=[
            SimpleNamespace(action="create_one", api_method="POST", api_path="/api/resources", body_template={}),
            SimpleNamespace(action="create_two", api_method="POST", api_path="/api/resources", body_template={}),
        ],
    )

    result = execute_with_sandbox_write(
        scenario,
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("qa"),
        execute_fn=lambda *args, **kwargs: calls.append("executed") or {"steps": []},
    )

    assert calls == []
    assert result["sandbox_write"]["status"] == "blocked"
    assert result["sandbox_write"]["reason"] == "multi_write_executor_missing_per_write_governance_hook"


def test_multi_write_execution_exception_still_cleans_and_audits_partial_setup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cleanup_paths: list[str] = []
    monkeypatch.setattr(
        "ai_test_asset_center.sandbox_write_executor._http_request",
        lambda *args, **kwargs: {"status": 200, "body": {}, "headers": {}},
    )

    def cleanup(**kwargs):
        cleanup_paths.append(str(kwargs["path"]))
        return {"status": "completed", "receipt_ref": kwargs["path"]}

    monkeypatch.setattr("ai_test_asset_center.sandbox_write_executor._cleanup_after_write", cleanup)
    scenario = SimpleNamespace(
        id="partial-multi-write",
        actor_token="test-token",
        actor_role="tester",
        behavior_slice_id="slice-partial-multi-write",
        execution_policy="approved_sandbox_write",
        steps=[
            SimpleNamespace(action="create_one", api_method="POST", api_path="/api/resources", body_template={}),
            SimpleNamespace(action="create_two", api_method="POST", api_path="/api/resources", body_template={}),
        ],
    )

    def execute(*args, write_observer, **kwargs):
        event_id = write_observer(
            "before",
            {
                "action": "create_one",
                "method": "POST",
                "path": "/api/resources",
                "body": {},
                "token": "test-token",
            },
        )
        write_observer(
            "after",
            {
                "event_id": event_id,
                "action": "create_one",
                "method": "POST",
                "path": "/api/resources",
                "status": 201,
                "response_body": {"id": "resource-1"},
                "token": "test-token",
            },
        )
        raise RuntimeError("second_write_binding_failed")

    result = execute_with_sandbox_write(
        scenario,
        "https://target.invalid",
        root=tmp_path,
        project="project",
        runtime_contract=_runtime("qa"),
        documented_routes=[
            {"method": "GET", "path": "/api/resources"},
            {"method": "POST", "path": "/api/resources"},
            {"method": "DELETE", "path": "/api/resources/{id}"},
        ],
        execute_fn=execute,
    )

    assert cleanup_paths == ["/api/resources"]
    assert result["sandbox_write"]["status"] == "cleanup_incomplete"
    assert result["sandbox_write"]["reason"] == "per_write_execution_failed"
    assert "RuntimeError:second_write_binding_failed" in result["sandbox_write"]["execution_exception"]
    assert result["sandbox_write"]["governed_write_receipt_count"] == 1
    assert Path(result["sandbox_write"]["audit_path"]).is_file()


def test_v12_write_scenario_is_not_retried_as_a_whole(monkeypatch) -> None:
    import ai_test_asset_center.v12_pipeline as v12_pipeline

    calls: list[str] = []

    def fail_once(*args, **kwargs):
        calls.append("attempted")
        raise RuntimeError("transport_outcome_unknown")

    monkeypatch.setattr(v12_pipeline, "__execute_scenario_once", fail_once)
    scenario = SimpleNamespace(
        id="write-no-retry",
        steps=[SimpleNamespace(action="create", api_method="POST", api_path="/api/resources")],
    )

    result = v12_pipeline._execute_scenario(scenario, "https://target.invalid", max_retries=3)

    assert calls == ["attempted"]
    assert result["errors"] == ["failed_after_retries:transport_outcome_unknown"]


def test_v12_executor_emits_before_and_after_hooks_for_each_runtime_write(monkeypatch) -> None:
    import json
    import ai_test_asset_center.v12_pipeline as v12_pipeline

    class Response:
        def __init__(self, status: int, body: dict):
            self.status = status
            self._body = body
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self, limit: int) -> bytes:
            return json.dumps(self._body).encode("utf-8")

    responses = [Response(201, {"id": "resource-1"}), Response(200, {"id": "resource-1", "value": 2})]
    monkeypatch.setattr(v12_pipeline.urllib.request, "urlopen", lambda *args, **kwargs: responses.pop(0))
    scenario = SimpleNamespace(
        id="hooked-writes",
        actor_token="test-token",
        steps=[
            SimpleNamespace(
                action="create",
                api_method="POST",
                api_path="/api/resources",
                body_template={"value": 1},
                extract_from_response=["id"],
            ),
            SimpleNamespace(
                action="update",
                api_method="PATCH",
                api_path="/api/resources/{id}",
                body_template={"value": 2},
                extract_from_response=[],
            ),
        ],
    )
    events: list[tuple[str, dict]] = []

    def observer(phase: str, payload: dict):
        events.append((phase, dict(payload)))
        if phase == "before":
            return sum(1 for observed_phase, _ in events if observed_phase == "before") - 1
        return None

    result = v12_pipeline._execute_scenario(
        scenario,
        "https://target.invalid",
        max_retries=3,
        write_observer=observer,
    )

    assert result["errors"] == []
    assert [phase for phase, _ in events] == ["before", "after", "before", "after"]
    assert events[2][1]["path"] == "/api/resources/resource-1"
    assert events[3][1]["status"] == 200
