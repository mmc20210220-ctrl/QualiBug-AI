from __future__ import annotations

import json
import urllib.request

import ai_test_asset_center.browser_execution as browser_execution
from ai_test_asset_center.page_agent_bridge import execute_page_agent_request
from ai_test_asset_center.page_agent_bridge_server import PageAgentBridgeApp


def test_execute_page_agent_request_forwards_browser_plan(tmp_path, monkeypatch) -> None:
    class _Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps({"status": "executed", "execution_status": "executed"}).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: _Response())

    result = execute_page_agent_request(
        "demo-project",
        {
            "request_id": "ui_req_1",
            "title": "Open orders page",
            "task": "Inspect orders",
            "start_url": "https://example.com/orders",
            "execution_mode": "safe_read_only",
            "browser_plan": {
                "execution_mode": "safe_read_only",
                "steps": [{"action": "goto", "url": "/orders"}],
            },
            "metadata": {
                "page_agent_bridge": {"url": "http://127.0.0.1:8797/execute"},
            },
        },
        {"status": "approved", "approved_base_url": "https://example.com", "execution_mode": "safe_read_only"},
        root=tmp_path,
        run_id="scan-run",
    )

    request_payload = json.loads(
        (
            tmp_path
            / "platform_workspace"
            / "demo-project"
            / "page_agent_runs"
            / "scan-run"
            / "ui_req_1"
            / "bridge_request.json"
        ).read_text(encoding="utf-8")
    )
    assert request_payload["browser_plan"]["steps"][0]["action"] == "goto"
    assert request_payload["browser_plan"]["steps"][0]["url"] == "/orders"
    assert result["status"] == "executed"


def test_page_agent_bridge_server_executes_explicit_browser_plan(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_execute_browser_plan(project_id, plan, runtime_contract, *, root, run_id="") -> dict[str, object]:
        captured["project_id"] = project_id
        captured["plan"] = plan
        captured["runtime_contract"] = runtime_contract
        captured["run_id"] = run_id
        return {
            "status": "executed",
            "reason": "",
            "execution_status": "executed",
            "confirmation_status": "candidate",
            "trace_ref": "platform_workspace/demo/trace.zip",
            "screenshot_ref": "platform_workspace/demo/final.png",
            "console": [],
            "network": [],
            "duration_ms": 12,
        }

    monkeypatch.setattr(browser_execution, "execute_browser_plan", fake_execute_browser_plan)
    app = PageAgentBridgeApp(root=tmp_path, mode="page_agent_browser_plan")

    result = app._execute(
        {
            "project_id": "demo-project",
            "request_id": "ui_req_2",
            "task": "Open orders and save filters",
            "start_url": "https://example.com/orders",
            "execution_mode": "approved_sandbox_write",
            "browser_plan": {
                "execution_mode": "approved_sandbox_write",
                "write_approved": True,
                "steps": [
                    {"action": "goto", "url": "/orders"},
                    {"action": "click", "selector": "[data-testid='save-filter']"},
                ],
            },
            "runtime_contract": {
                "status": "approved",
                "approved_base_url": "https://example.com",
                "execution_mode": "approved_sandbox_write",
            },
        }
    )

    plan = captured["plan"]
    assert isinstance(plan, dict)
    assert plan["steps"][1]["action"] == "click"
    assert captured["runtime_contract"]["approved_base_url"] == "https://example.com"
    assert result["status"] == "executed"
    assert result["artifacts"][0]["artifact_type"] == "trace"


def test_page_agent_bridge_server_derives_safe_read_observation_plan(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_execute_browser_plan(project_id, plan, runtime_contract, *, root, run_id="") -> dict[str, object]:
        captured["plan"] = plan
        return {
            "status": "executed",
            "reason": "",
            "execution_status": "executed",
            "confirmation_status": "candidate",
            "trace_ref": "platform_workspace/demo/trace.zip",
            "screenshot_ref": "platform_workspace/demo/final.png",
            "console": [],
            "network": [],
            "duration_ms": 8,
        }

    monkeypatch.setattr(browser_execution, "execute_browser_plan", fake_execute_browser_plan)
    app = PageAgentBridgeApp(root=tmp_path, mode="page_agent_browser_plan")

    result = app._execute(
        {
            "project_id": "demo-project",
            "request_id": "ui_req_3",
            "task": "Observe orders page",
            "start_url": "https://example.com/orders",
            "execution_mode": "safe_read_only",
            "success_criteria": {"texts": ["Orders"]},
            "runtime_contract": {
                "status": "approved",
                "approved_base_url": "https://example.com",
                "execution_mode": "safe_read_only",
            },
        }
    )

    plan = captured["plan"]
    assert isinstance(plan, dict)
    assert [step["action"] for step in plan["steps"]] == ["goto", "wait_for_load", "expect_text", "screenshot"]
    assert plan["steps"][2]["selector"] == "body"
    assert result["status"] == "executed"


def test_page_agent_bridge_server_blocks_write_without_explicit_plan(tmp_path) -> None:
    app = PageAgentBridgeApp(root=tmp_path, mode="page_agent_browser_plan")

    result = app._execute(
        {
            "project_id": "demo-project",
            "request_id": "ui_req_4",
            "task": "Create order from UI",
            "start_url": "https://example.com/orders/new",
            "execution_mode": "approved_sandbox_write",
            "runtime_contract": {
                "status": "approved",
                "approved_base_url": "https://example.com",
                "execution_mode": "approved_sandbox_write",
            },
        }
    )

    assert result["status"] == "blocked"
    assert result["reason"] == "PAGE_AGENT_ACTION_PLAN_MISSING"
