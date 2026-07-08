from __future__ import annotations

import pytest

import ai_test_asset_center.browser_execution as browser_execution
from ai_test_asset_center.browser_execution import BrowserExecutionError, execute_browser_plan, validate_browser_plan


CONTRACT = {
    "status": "approved",
    "approved_base_url": "https://test.example.invalid",
    "source_manifest": {"source_id": "ui-map", "source_hash": "a" * 64},
}


def test_safe_read_only_browser_plan_accepts_observation_steps():
    plan = validate_browser_plan(
        {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/dashboard"},
                {"action": "expect_text", "selector": "main", "text": "Overview"},
                {"action": "screenshot"},
            ],
        },
        CONTRACT,
    )
    assert plan["steps"][0]["url"] == "https://test.example.invalid/dashboard"
    assert plan["execution_mode"] == "safe_read_only"


def test_safe_read_only_browser_plan_rejects_interaction():
    with pytest.raises(BrowserExecutionError, match="browser_interaction_requires_approval"):
        validate_browser_plan(
            {"execution_mode": "safe_read_only", "steps": [{"action": "click", "selector": "button"}]},
            CONTRACT,
        )


def test_sandbox_interaction_requires_explicit_write_approval():
    with pytest.raises(BrowserExecutionError, match="browser_write_approval_missing"):
        validate_browser_plan(
            {"execution_mode": "approved_sandbox_write", "steps": [{"action": "click", "selector": "button"}]},
            CONTRACT,
        )

    plan = validate_browser_plan(
        {"execution_mode": "approved_sandbox_write", "write_approved": True, "steps": [{"action": "click", "selector": "button"}]},
        CONTRACT,
    )
    assert plan["steps"][0]["action"] == "click"


def test_browser_target_cannot_escape_approved_base_url():
    for unsafe_url in ("https://other.example.invalid/", "https://test.example.invalid.evil/"):
        with pytest.raises(BrowserExecutionError, match="browser_target_outside_approved_base_url"):
            validate_browser_plan({"steps": [{"action": "goto", "url": unsafe_url}]}, CONTRACT)


def test_execute_browser_plan_uses_auto_browser_setup_runtime(tmp_path, monkeypatch):
    state: dict[str, object] = {}

    class _Response:
        status = 200

    class _Tracing:
        def start(self, **kwargs) -> None:
            state["tracing_started"] = kwargs

        def stop(self, *, path: str) -> None:
            state["trace_path"] = path
            tmp_path.joinpath("platform_workspace", "demo-project", "browser_runs", "run-1", "trace.zip").write_text("trace", encoding="utf-8")

    class _Page:
        def on(self, event: str, callback) -> None:
            state.setdefault("events", []).append(event)

        def goto(self, url: str, **kwargs):
            state["goto_url"] = url
            return _Response()

        def wait_for_load_state(self, state_name: str, **kwargs) -> None:
            state["load_state"] = state_name

        def screenshot(self, *, path: str, full_page: bool) -> None:
            state.setdefault("screenshots", []).append(path)
            tmp_path.joinpath(path).write_text("png", encoding="utf-8")

    class _Context:
        def __init__(self) -> None:
            self.tracing = _Tracing()

        def new_page(self) -> _Page:
            return _Page()

        def close(self) -> None:
            state["context_closed"] = True

    class _Browser:
        def new_context(self) -> _Context:
            state["context_created"] = True
            return _Context()

        def close(self) -> None:
            state["browser_closed"] = True

    class _PlaywrightRuntime:
        def stop(self) -> None:
            state["runtime_stopped"] = True

    def fake_ensure_browser(headless: bool = True, timeout: int = 30000):
        state["ensure_browser"] = {"headless": headless, "timeout": timeout}
        return _PlaywrightRuntime(), _Browser()

    monkeypatch.setattr("ai_test_asset_center.auto_browser_setup.ensure_browser", fake_ensure_browser)

    result = execute_browser_plan(
        "demo-project",
        {
            "execution_mode": "safe_read_only",
            "steps": [
                {"action": "goto", "url": "/orders"},
                {"action": "wait_for_load", "state": "networkidle"},
                {"action": "screenshot", "full_page": True},
            ],
        },
        CONTRACT,
        root=tmp_path,
        run_id="run-1",
    )

    assert state["ensure_browser"] == {"headless": True, "timeout": 30000}
    assert state["goto_url"] == "https://test.example.invalid/orders"
    assert result["status"] == "executed"
    assert result["trace_ref"].endswith("trace.zip")
    assert result["screenshot_ref"].endswith("final.png")
    assert state["browser_closed"] is True
    assert state["runtime_stopped"] is True


def test_execute_browser_plan_reports_bootstrap_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ai_test_asset_center.auto_browser_setup.ensure_browser",
        lambda headless=True, timeout=30000: (None, "chromium cache missing"),
    )

    result = execute_browser_plan(
        "demo-project",
        {"execution_mode": "safe_read_only", "steps": [{"action": "goto", "url": "/orders"}]},
        CONTRACT,
        root=tmp_path,
        run_id="run-2",
    )

    assert result["status"] == "blocked"
    assert result["execution_status"] == "not_executed"
    assert result["reason"].startswith("BROWSER_RUNTIME_UNAVAILABLE:chromium cache missing")
