from __future__ import annotations

import json
import sys
from types import ModuleType
from pathlib import Path

from ai_test_asset_center.browser_ui_replay_discovery_adapter import collect_browser_ui_replay_issues, generate_browser_ui_replay_probes


def test_browser_ui_replay_adapter_emits_ui_issues_without_playwright(monkeypatch, tmp_path: Path) -> None:
    class _FakeExplorer:
        def explore(self, config, workspace, output):
            return {
                "page_map": {
                    "status": "explored",
                    "base_url": config.base_url,
                    "pages": [{"url": config.base_url, "title": "", "links": [], "text_fragments": [], "screenshot": "x.png"}],
                    "edges": [],
                },
                "element_map": {"status": "explored", "elements": []},
            }

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.project = kwargs.get("project")
            self.base_url = kwargs.get("base_url")
            self.execute_browser = kwargs.get("execute_browser")
            self.browser = kwargs.get("browser")
            self.headless = kwargs.get("headless")
            self.max_pages = kwargs.get("max_pages")

    fake_module = ModuleType("aitestops.ui_journey_tester")
    fake_module.BrowserExplorer = _FakeExplorer
    fake_module.UIJourneyConfig = _FakeConfig
    monkeypatch.setitem(sys.modules, "aitestops.ui_journey_tester", fake_module)

    probes = generate_browser_ui_replay_probes({}, {"execute_browser_ui": True}, "demo")
    assert probes and probes[0]["defect_family"] == "ui"

    issues = collect_browser_ui_replay_issues("demo", root=tmp_path, cfg={"execute_browser_ui": True, "base_url": "http://example"}, scenario="manufacturing")
    families = {issue["defect_family"] for issue in issues}
    assert "ui" in families
    assert "uiux" in families


def test_browser_ui_replay_adapter_loads_frontend_task_journeys_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "frontend_task_journeys_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "phase106d-frontend-project-routes-v1",
                "journeys": [
                    {
                        "journey_id": "enter_command_center",
                        "title": "进入质量驾驶舱",
                        "entry_route": "/projects/:projectId",
                        "required_project_context": True,
                        "steps": ["load_project_detail", "open_command_center"],
                        "success_signals": ["command_center_link_visible", "route_navigation_success"],
                        "failure_signals": ["missing_cta", "project_context_lost"],
                        "defect_family": "uiux",
                        "risk_tags": ["journey_break"],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    probes = generate_browser_ui_replay_probes(
        {},
        {"execute_browser_ui": True, "frontend_task_journeys_manifest": str(manifest_path)},
        "demo",
        root=tmp_path,
    )

    journey_probes = [probe for probe in probes if probe.get("source") == "frontend_task_journey"]
    assert journey_probes
    assert journey_probes[0]["path"] == "/projects/:projectId"
    assert journey_probes[0]["defect_family"] == "uiux"
    assert journey_probes[0]["evidence"]["journey_id"] == "enter_command_center"


def test_browser_ui_replay_adapter_emits_design_oracle_issues(monkeypatch, tmp_path: Path) -> None:
    class _FakeExplorer:
        def explore(self, config, workspace, output):
            return {
                "page_map": {
                    "status": "explored",
                    "base_url": config.base_url,
                    "pages": [
                        {
                            "url": f"{config.base_url}/projects",
                            "title": "项目列表",
                            "links": [],
                            "text_fragments": ["项目列表"],
                            "screenshot": "x.png",
                        }
                    ],
                    "edges": [],
                },
                "element_map": {"status": "explored", "elements": []},
            }

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.project = kwargs.get("project")
            self.base_url = kwargs.get("base_url")
            self.execute_browser = kwargs.get("execute_browser")
            self.browser = kwargs.get("browser")
            self.headless = kwargs.get("headless")
            self.max_pages = kwargs.get("max_pages")

    fake_module = ModuleType("aitestops.ui_journey_tester")
    fake_module.BrowserExplorer = _FakeExplorer
    fake_module.UIJourneyConfig = _FakeConfig
    monkeypatch.setitem(sys.modules, "aitestops.ui_journey_tester", fake_module)

    oracle_path = tmp_path / "ui_design_oracle_manifest.json"
    oracle_path.write_text(
        json.dumps(
            {
                "version": "ui-design-oracle-v1",
                "screens": [
                    {
                        "screen_id": "project_list",
                        "route": "/projects",
                        "expected_components": ["project_switcher"],
                        "required_feedback": [],
                    }
                ],
                "journeys": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    issues = collect_browser_ui_replay_issues(
        "demo",
        root=tmp_path,
        cfg={"execute_browser_ui": True, "base_url": "http://example", "ui_design_oracle_manifest": str(oracle_path)},
        scenario="manufacturing",
    )
    assert any(issue.get("source") == "ui_design_oracle" for issue in issues)


def test_browser_ui_design_oracle_structured_match_suppresses_missing_component(monkeypatch, tmp_path: Path) -> None:
    class _FakeExplorer:
        def explore(self, config, workspace, output):
            return {
                "page_map": {
                    "status": "explored",
                    "base_url": config.base_url,
                    "pages": [
                        {
                            "url": f"{config.base_url}/projects",
                            "title": "项目列表",
                            "links": [],
                            "text_fragments": ["项目列表"],
                            "screenshot": "x.png",
                        }
                    ],
                    "edges": [],
                },
                "element_map": {
                    "status": "explored",
                    "elements": [
                        {"role": "combobox", "label": "当前项目切换", "testid": "project-switcher"},
                    ],
                },
            }

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.project = kwargs.get("project")
            self.base_url = kwargs.get("base_url")
            self.execute_browser = kwargs.get("execute_browser")
            self.browser = kwargs.get("browser")
            self.headless = kwargs.get("headless")
            self.max_pages = kwargs.get("max_pages")

    fake_module = ModuleType("aitestops.ui_journey_tester")
    fake_module.BrowserExplorer = _FakeExplorer
    fake_module.UIJourneyConfig = _FakeConfig
    monkeypatch.setitem(sys.modules, "aitestops.ui_journey_tester", fake_module)

    oracle_path = tmp_path / "ui_design_oracle_manifest.json"
    oracle_path.write_text(
        json.dumps(
            {
                "version": "ui-design-oracle-v1",
                "screens": [
                    {
                        "screen_id": "project_list",
                        "route": "/projects",
                        "expected_components": ["project_switcher"],
                        "required_feedback": [],
                    }
                ],
                "journeys": [],
                "match_hints": {
                    "project_switcher": {
                        "roles": ["combobox"],
                        "testids": ["project-switcher"],
                        "keywords": ["当前项目切换"],
                        "tokens": ["当前项目切换"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    issues = collect_browser_ui_replay_issues(
        "demo",
        root=tmp_path,
        cfg={"execute_browser_ui": True, "base_url": "http://example", "ui_design_oracle_manifest": str(oracle_path)},
        scenario="manufacturing",
    )
    assert not any(
        issue.get("source") == "ui_design_oracle"
        and isinstance(issue.get("evidence"), dict)
        and (issue.get("evidence") or {}).get("missing_component") == "project_switcher"
        for issue in issues
    )


def test_browser_ui_design_oracle_structured_match_accepts_testid_in_selector(monkeypatch, tmp_path: Path) -> None:
    class _FakeExplorer:
        def explore(self, config, workspace, output):
            return {
                "page_map": {
                    "status": "explored",
                    "base_url": config.base_url,
                    "pages": [{"url": f"{config.base_url}/projects", "title": "项目列表", "links": [], "text_fragments": [], "screenshot": "x.png"}],
                    "edges": [],
                },
                "element_map": {
                    "status": "explored",
                    "elements": [
                        {"role": "combobox", "selector": "label[data-testid='project-switcher'] select"},
                    ],
                },
            }

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.project = kwargs.get("project")
            self.base_url = kwargs.get("base_url")
            self.execute_browser = kwargs.get("execute_browser")
            self.browser = kwargs.get("browser")
            self.headless = kwargs.get("headless")
            self.max_pages = kwargs.get("max_pages")

    fake_module = ModuleType("aitestops.ui_journey_tester")
    fake_module.BrowserExplorer = _FakeExplorer
    fake_module.UIJourneyConfig = _FakeConfig
    monkeypatch.setitem(sys.modules, "aitestops.ui_journey_tester", fake_module)

    oracle_path = tmp_path / "ui_design_oracle_manifest.json"
    oracle_path.write_text(
        json.dumps(
            {
                "version": "ui-design-oracle-v1",
                "screens": [{"screen_id": "project_list", "route": "/projects", "expected_components": ["project_switcher"], "required_feedback": []}],
                "journeys": [],
                "match_hints": {"project_switcher": {"roles": ["combobox"], "testids": ["project-switcher"], "keywords": [], "tokens": []}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    issues = collect_browser_ui_replay_issues(
        "demo",
        root=tmp_path,
        cfg={"execute_browser_ui": True, "base_url": "http://example", "ui_design_oracle_manifest": str(oracle_path)},
        scenario="manufacturing",
    )
    assert not any(
        issue.get("source") == "ui_design_oracle"
        and isinstance(issue.get("evidence"), dict)
        and (issue.get("evidence") or {}).get("missing_component") == "project_switcher"
        for issue in issues
    )


def test_browser_ui_design_oracle_structured_match_accepts_testid_in_css_candidates(monkeypatch, tmp_path: Path) -> None:
    class _FakeExplorer:
        def explore(self, config, workspace, output):
            return {
                "page_map": {
                    "status": "explored",
                    "base_url": config.base_url,
                    "pages": [{"url": f"{config.base_url}/projects", "title": "项目列表", "links": [], "text_fragments": [], "screenshot": "x.png"}],
                    "edges": [],
                },
                "element_map": {
                    "status": "explored",
                    "elements": [
                        {"role": "combobox", "css_candidates": ["[data-testid='project-switcher']", "select"]},
                    ],
                },
            }

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.project = kwargs.get("project")
            self.base_url = kwargs.get("base_url")
            self.execute_browser = kwargs.get("execute_browser")
            self.browser = kwargs.get("browser")
            self.headless = kwargs.get("headless")
            self.max_pages = kwargs.get("max_pages")

    fake_module = ModuleType("aitestops.ui_journey_tester")
    fake_module.BrowserExplorer = _FakeExplorer
    fake_module.UIJourneyConfig = _FakeConfig
    monkeypatch.setitem(sys.modules, "aitestops.ui_journey_tester", fake_module)

    oracle_path = tmp_path / "ui_design_oracle_manifest.json"
    oracle_path.write_text(
        json.dumps(
            {
                "version": "ui-design-oracle-v1",
                "screens": [{"screen_id": "project_list", "route": "/projects", "expected_components": ["project_switcher"], "required_feedback": []}],
                "journeys": [],
                "match_hints": {"project_switcher": {"roles": ["combobox"], "testids": ["project-switcher"], "keywords": [], "tokens": []}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    issues = collect_browser_ui_replay_issues(
        "demo",
        root=tmp_path,
        cfg={"execute_browser_ui": True, "base_url": "http://example", "ui_design_oracle_manifest": str(oracle_path)},
        scenario="manufacturing",
    )
    assert not any(
        issue.get("source") == "ui_design_oracle"
        and isinstance(issue.get("evidence"), dict)
        and (issue.get("evidence") or {}).get("missing_component") == "project_switcher"
        for issue in issues
    )


def test_browser_ui_design_oracle_confidence_increases_when_testids_provided(monkeypatch, tmp_path: Path) -> None:
    class _FakeExplorer:
        def explore(self, config, workspace, output):
            return {
                "page_map": {
                    "status": "explored",
                    "base_url": config.base_url,
                    "pages": [{"url": f"{config.base_url}/projects", "title": "项目列表", "links": [], "text_fragments": [], "screenshot": "x.png"}],
                    "edges": [],
                },
                "element_map": {
                    "status": "explored",
                    "elements": [{"role": "combobox", "selector": "label[data-testid='unrelated'] select"}],
                },
            }

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.project = kwargs.get("project")
            self.base_url = kwargs.get("base_url")
            self.execute_browser = kwargs.get("execute_browser")
            self.browser = kwargs.get("browser")
            self.headless = kwargs.get("headless")
            self.max_pages = kwargs.get("max_pages")

    fake_module = ModuleType("aitestops.ui_journey_tester")
    fake_module.BrowserExplorer = _FakeExplorer
    fake_module.UIJourneyConfig = _FakeConfig
    monkeypatch.setitem(sys.modules, "aitestops.ui_journey_tester", fake_module)

    oracle_path = tmp_path / "ui_design_oracle_manifest.json"
    oracle_path.write_text(
        json.dumps(
            {
                "version": "ui-design-oracle-v1",
                "screens": [{"screen_id": "project_list", "route": "/projects", "expected_components": ["project_switcher"], "required_feedback": []}],
                "journeys": [],
                "match_hints": {"project_switcher": {"roles": ["combobox"], "testids": ["project-switcher"], "keywords": [], "tokens": []}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    issues = collect_browser_ui_replay_issues(
        "demo",
        root=tmp_path,
        cfg={"execute_browser_ui": True, "base_url": "http://example", "ui_design_oracle_manifest": str(oracle_path)},
        scenario="manufacturing",
    )
    candidates = [
        issue
        for issue in issues
        if issue.get("source") == "ui_design_oracle" and isinstance(issue.get("evidence"), dict) and (issue.get("evidence") or {}).get("missing_component") == "project_switcher"
    ]
    assert candidates
    assert float(candidates[0].get("confidence") or 0.0) >= 0.72


def test_browser_ui_design_oracle_emits_journey_level_issue(monkeypatch, tmp_path: Path) -> None:
    class _FakeExplorer:
        def explore(self, config, workspace, output):
            return {
                "page_map": {
                    "status": "explored",
                    "base_url": config.base_url,
                    "pages": [{"url": f"{config.base_url}/projects", "title": "项目列表", "links": [], "text_fragments": [], "screenshot": "x.png"}],
                    "edges": [],
                },
                "element_map": {"status": "explored", "elements": []},
            }

    class _FakeConfig:
        def __init__(self, **kwargs):
            self.project = kwargs.get("project")
            self.base_url = kwargs.get("base_url")
            self.execute_browser = kwargs.get("execute_browser")
            self.browser = kwargs.get("browser")
            self.headless = kwargs.get("headless")
            self.max_pages = kwargs.get("max_pages")

    fake_module = ModuleType("aitestops.ui_journey_tester")
    fake_module.BrowserExplorer = _FakeExplorer
    fake_module.UIJourneyConfig = _FakeConfig
    monkeypatch.setitem(sys.modules, "aitestops.ui_journey_tester", fake_module)

    oracle_path = tmp_path / "ui_design_oracle_manifest.json"
    oracle_path.write_text(
        json.dumps(
            {
                "version": "ui-design-oracle-v1",
                "screens": [{"screen_id": "project_list", "route": "/projects", "expected_components": [], "required_feedback": []}],
                "journeys": [
                    {
                        "journey_id": "select_project",
                        "title": "切换当前项目",
                        "entry_route": "/projects",
                        "required_components": ["project_switcher"],
                        "expected_feedback": ["selected_project_persisted"],
                        "defect_family": "uiux",
                    }
                ],
                "match_hints": {
                    "project_switcher": {"roles": ["combobox"], "testids": ["project-switcher"], "keywords": [], "tokens": []},
                    "selected_project_persisted": {"tokens": ["qualibug.selectedProjectId"]},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    issues = collect_browser_ui_replay_issues(
        "demo",
        root=tmp_path,
        cfg={"execute_browser_ui": True, "base_url": "http://example", "ui_design_oracle_manifest": str(oracle_path)},
        scenario="manufacturing",
    )
    assert any(
        issue.get("source") == "ui_design_oracle"
        and isinstance(issue.get("evidence"), dict)
        and (issue.get("evidence") or {}).get("journey_id") == "select_project"
        for issue in issues
    )
