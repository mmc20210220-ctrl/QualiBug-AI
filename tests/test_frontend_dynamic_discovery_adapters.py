from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_test_asset_center.compatibility_discovery_adapter import collect_compatibility_issues
from ai_test_asset_center.frontend_runtime_discovery_adapter import collect_frontend_runtime_issues
from ai_test_asset_center.frontend_ux_discovery_adapter import collect_frontend_ux_issues


@dataclass
class _FakeReport:
    payload: dict

    def to_dict(self) -> dict:
        return self.payload


def test_dynamic_frontend_adapters_surface_ui_and_compatibility_issues(monkeypatch, tmp_path: Path) -> None:
    from ai_test_asset_center import frontend_runtime_discovery_adapter as runtime_adapter
    from ai_test_asset_center import frontend_ux_discovery_adapter as ux_adapter
    from ai_test_asset_center import compatibility_discovery_adapter as compat_adapter

    monkeypatch.setattr(
        runtime_adapter,
        "run_frontend_runtime_smoke",
        lambda **_: _FakeReport(
            {
                "score": 70,
                "redaction_status": "safe",
                "failed_step_count": 1,
                "steps": [{"key": "generate_report", "passed": False, "status": 500, "path": "/api/v1/projects/demo/reports/generate", "detail": "report generation failed"}],
            }
        ),
    )
    monkeypatch.setattr(runtime_adapter, "validate_frontend_execution_runtime", lambda *_, **__: _FakeReport({"checks": [], "artifacts": {}}))
    monkeypatch.setattr(
        ux_adapter,
        "run_frontend_interaction_acceptance",
        lambda **_: {
            "acceptance": {
                "score": 82,
                "checks": [{"key": "customer_actions", "passed": False, "severity": "critical", "detail": "缺少主任务 CTA"}],
            }
        },
    )
    monkeypatch.setattr(
        compat_adapter,
        "run_frontend_preview_acceptance",
        lambda **_: _FakeReport(
            {
                "checks": [{"key": "static_page_routes", "passed": False, "severity": "critical", "detail": "页面路由异常"}],
                "artifacts": {"preview_url": "http://127.0.0.1:8795/"},
            }
        ),
    )

    runtime_issues = collect_frontend_runtime_issues("demo", root=tmp_path, scenario="manufacturing", cfg={"base_url": "http://127.0.0.1:8790"})
    ux_issues = collect_frontend_ux_issues("demo", root=tmp_path, scenario="manufacturing", cfg={"base_url": "http://127.0.0.1:8790"})
    compat_issues = collect_compatibility_issues({"environment_class": "sandbox", "deployment_mode": "private_deployment"}, openapi={"openapi": "3.0.0"}, project_id="demo", root=tmp_path)

    assert any(issue["defect_family"] == "uiux" for issue in runtime_issues)
    assert any(issue["defect_family"] == "uiux" for issue in ux_issues)
    assert any(issue["defect_family"] == "compatibility" for issue in compat_issues)

