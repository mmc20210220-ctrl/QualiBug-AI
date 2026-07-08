"""主链 5 regression: the REAL execution engine must honor the customer's
production-data safety boundary (主链 1: 生产数据禁触).

Before this fix, v12_pipeline.__execute_scenario_once fired live HTTP requests
for every planned step and NEVER consulted match_production_data_exclusion — so a
customer who configured "禁止触碰 /api/admin/..." could still have the executor
hit those endpoints. The fix reuses the SAME matcher grounded_probe_executor uses
(single source of truth) and blocks the step before any request leaves the process.
"""
from __future__ import annotations

import json

from ai_test_asset_center.v12_pipeline import (
    _execute_scenario,
    _load_execution_safety_boundary,
    match_production_data_exclusion,
)


class _FakeStep:
    def __init__(self, action, method, api_path, *, body_template=None,
                 expected_status=200, risk_type=""):
        self.action = action
        self.api_method = method
        self.api_path = api_path
        self.body_template = body_template or {}
        self.expected_status = expected_status
        self.risk_type = risk_type
        self.extract_from_response = []
        self.actor = ""


class _FakeScenario:
    def __init__(self, steps):
        self.id = "SCN_1"
        self.steps = steps
        self.actor_token = ""
        self.behavior_slice_id = "BHV_1"


class _FakeResp:
    def __init__(self, body=b"{}", status=200):
        self._body = body
        self.status = status
        self.headers = {}

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _spy_urlopen(calls):
    def _fake(request, timeout=10):
        calls.append(request.full_url)
        return _FakeResp()
    return _fake


def test_excluded_step_is_blocked_and_never_touches_network(monkeypatch):
    """主链 5 × 主链 1: a step matching the customer's exclusion list is recorded
    as blocked and NO HTTP request is fired (block only, never enable)."""
    calls: list[str] = []
    monkeypatch.setattr("urllib.request.urlopen", _spy_urlopen(calls))
    scenario = _FakeScenario([_FakeStep("forbidden_read", "GET", "/api/admin/production-users")])
    boundary = {"production_data_exclusion": ["/api/admin/"]}
    trace = _execute_scenario(scenario, "http://target.local", safety_boundary=boundary)
    assert calls == [], "excluded step must NOT reach the network"
    assert trace.get("production_data_blocked") is True
    step = trace["steps"][0]
    assert step.get("execution_blocked") is True
    assert step["status"] == 0
    assert step["skipped_reason"].startswith("production_data_exclusion_matched:")


def test_allowed_step_executes_against_network(monkeypatch):
    """A step outside the exclusion list still executes normally."""
    calls: list[str] = []
    monkeypatch.setattr("urllib.request.urlopen", _spy_urlopen(calls))
    scenario = _FakeScenario([_FakeStep("list_products", "GET", "/api/products")])
    boundary = {"production_data_exclusion": ["/api/admin/"]}
    trace = _execute_scenario(scenario, "http://target.local", safety_boundary=boundary)
    assert len(calls) == 1, "allowed step must execute"
    assert trace.get("production_data_blocked") is not True
    assert trace["steps"][0].get("execution_blocked") is not True


def test_no_boundary_configured_means_no_blocking(monkeypatch):
    """When no safety boundary is configured, execution proceeds (matcher no-op)."""
    calls: list[str] = []
    monkeypatch.setattr("urllib.request.urlopen", _spy_urlopen(calls))
    scenario = _FakeScenario([_FakeStep("admin_read", "GET", "/api/admin/users")])
    trace = _execute_scenario(scenario, "http://target.local", safety_boundary=None)
    assert len(calls) == 1
    assert trace.get("production_data_blocked") is not True


def test_exclusion_matches_by_risk_type(monkeypatch):
    """Exclusion can match on risk_type even when the path is generic."""
    calls: list[str] = []
    monkeypatch.setattr("urllib.request.urlopen", _spy_urlopen(calls))
    scenario = _FakeScenario([_FakeStep("touch_pii", "GET", "/internal/query", risk_type="production_pii")])
    boundary = {"production_data_exclusion": ["production_pii"]}
    trace = _execute_scenario(scenario, "http://target.local", safety_boundary=boundary)
    assert calls == []
    assert trace["steps"][0].get("execution_blocked") is True


def test_load_execution_safety_boundary_reads_project_config(tmp_path):
    """主链 1 metadata (production_data_exclusion) is loaded into the matcher shape."""
    project = "mc5_proj"
    cfg_dir = tmp_path / "platform_workspace" / project
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "multi_service_config.json").write_text(json.dumps({
        "project_name": project,
        "production_data_exclusion": ["/api/admin/", "re:/settlement/.*"],
    }), encoding="utf-8")
    boundary = _load_execution_safety_boundary(project, tmp_path)
    assert boundary == {"production_data_exclusion": ["/api/admin/", "re:/settlement/.*"]}
    # sanity: the loaded boundary actually guards via the shared matcher
    assert match_production_data_exclusion(boundary, "http://x/api/admin/users", "") is not None
    assert match_production_data_exclusion(boundary, "http://x/api/products", "") is None


def test_load_execution_safety_boundary_empty_when_unconfigured(tmp_path):
    project = "mc5_empty"
    cfg_dir = tmp_path / "platform_workspace" / project
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "multi_service_config.json").write_text(json.dumps({"project_name": project}), encoding="utf-8")
    assert _load_execution_safety_boundary(project, tmp_path) == {}

    project2 = "mc5_no_cfg"
    assert _load_execution_safety_boundary(project2, tmp_path) == {}
