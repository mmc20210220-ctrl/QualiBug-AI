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
