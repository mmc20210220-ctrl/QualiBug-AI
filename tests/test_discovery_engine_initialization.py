from __future__ import annotations

from types import SimpleNamespace

import ai_test_asset_center.discovery_engine as discovery_engine


def _isolate_initialization(monkeypatch) -> None:
    client = SimpleNamespace(
        config=SimpleNamespace(model="", max_tokens=1, timeout_seconds=1),
    )
    monkeypatch.setattr(discovery_engine, "_get_client", lambda: client)
    monkeypatch.setattr(discovery_engine, "resolve_budget_learning_context", lambda **kwargs: {})
    monkeypatch.setattr(
        discovery_engine,
        "build_deployment_config_snapshot",
        lambda context: {"project_id": "isolated-project"},
    )
    monkeypatch.setattr(discovery_engine, "load_deployment_config_snapshot", lambda project_id: {})
    monkeypatch.setattr(discovery_engine, "detect_deployment_config_drift", lambda current, previous: {})
    monkeypatch.setattr(discovery_engine, "evaluate_deployment_drift_unlock", lambda current, drift: {})
    monkeypatch.setattr(discovery_engine, "load_budget_feedback_profile", lambda context: {})


def test_engine_initializes_project_root_and_required_guardrails(tmp_path, monkeypatch) -> None:
    _isolate_initialization(monkeypatch)
    monkeypatch.setenv("QUALIBUG_PRODUCTION", "1")
    monkeypatch.delenv("QUALIBUG_DEFAULT_BASE_URL", raising=False)

    engine = discovery_engine.AutonomousDiscoveryEngine(project_id="customer-project", root=tmp_path)

    assert engine.base == "http://127.0.0.1:8088"
    assert engine._project == "customer-project"
    assert engine._root == tmp_path.resolve()
    assert engine.client.config.timeout_seconds >= 300
    assert engine.client.config.max_tokens >= 32768
    assert engine._tokens_authentic is False


def test_failed_runtime_login_is_not_overwritten_as_authentic(tmp_path, monkeypatch) -> None:
    _isolate_initialization(monkeypatch)
    monkeypatch.delenv("QUALIBUG_PRODUCTION", raising=False)
    monkeypatch.setattr(
        discovery_engine.AutonomousDiscoveryEngine,
        "_init_multi_module_auth",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_engine.AutonomousDiscoveryEngine,
        "_login",
        lambda self: False,
    )

    engine = discovery_engine.AutonomousDiscoveryEngine(project_id="customer-project", root=tmp_path)

    assert engine._service_tokens == {}
    assert engine._tokens_authentic is False


def test_admin_token_is_never_relabelled_as_viewer_identity(tmp_path, monkeypatch) -> None:
    _isolate_initialization(monkeypatch)
    monkeypatch.delenv("QUALIBUG_PRODUCTION", raising=False)
    monkeypatch.setenv("QUALIBUG_ADMIN_USER", "admin")
    monkeypatch.setenv("QUALIBUG_ADMIN_PASS", "secret")
    monkeypatch.delenv("QUALIBUG_VIEWER_USER", raising=False)
    monkeypatch.delenv("QUALIBUG_VIEWER_PASS", raising=False)
    monkeypatch.delenv("QUALIBUG_VIEWER_TOKEN", raising=False)
    monkeypatch.setattr(
        discovery_engine.AutonomousDiscoveryEngine,
        "_init_multi_module_auth",
        lambda self: None,
    )
    monkeypatch.setattr(
        discovery_engine.AutonomousDiscoveryEngine,
        "_http",
        lambda self, method, path, **kwargs: {"data": {"accessToken": "real-admin-token"}},
    )

    engine = discovery_engine.AutonomousDiscoveryEngine(project_id="customer-project", root=tmp_path)

    assert engine._tokens == {"admin": "real-admin-token"}
    assert engine._tokens_authentic is True
    assert engine._auth_warnings == ["viewer_identity_missing"]


def test_missing_viewer_identity_blocks_viewer_request_before_network(monkeypatch) -> None:
    engine = object.__new__(discovery_engine.AutonomousDiscoveryEngine)
    engine.base = "https://target.invalid"
    engine._production_blocked = False
    engine._service_tokens = {}
    engine._credential_manager = None
    engine._tokens = {"admin": "real-admin-token"}
    calls: list[str] = []
    monkeypatch.setattr(
        discovery_engine.urllib.request,
        "urlopen",
        lambda *args, **kwargs: calls.append("network") or None,
    )

    result = engine._http("GET", "/api/resources", role="viewer")

    assert calls == []
    assert result["_http"] == 0
    assert result["_error"] == "role_token_missing:viewer"
