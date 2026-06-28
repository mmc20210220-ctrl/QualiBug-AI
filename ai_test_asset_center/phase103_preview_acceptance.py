from __future__ import annotations

"""Phase103W: preview acceptance validator for Enterprise Command Center.

The Phase103 preview stack can already seed demo data, export static pages, and
serve a small local API.  This module adds a customer-safe acceptance gate around
that stack so teams can validate a build before showing it to customers,
shipping a demo bundle, or wiring a real frontend.

It is dependency-free and browserless by design: the validator calls the same
``Phase103PreviewSite.route`` path used by the local server, checks required
static pages and V1 API routes, verifies redaction, and writes a concise JSON +
Markdown report for product, sales, QA, and implementation teams.
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase103_preview_server import Phase103PreviewSite

PHASE103W_VERSION = "phase103w-preview-acceptance-v1"

DEFAULT_SECRET_PATTERNS: tuple[str, ...] = (
    "raw-manufacturing-token",
    "raw-ecommerce-token",
    "raw-saas-token",
    "DemoPasswordShouldBeRedacted",
    "SESSION=raw",
    "refresh-token-raw",
    "raw-client-secret",
    "access_token=",
    "refresh_token=",
)

REQUIRED_PAGES: tuple[tuple[str, str], ...] = (
    ("/index.html", "QualiBug AI"),
    ("/dashboard.html", "质量驾驶舱"),
    ("/environment.html", "环境适配"),
    ("/test_plan.html", "AI 测试计划"),
    ("/live_map.html", "实时测试地图"),
    ("/risks.html", "AI 风险发现"),
    ("/report.html", "成果战报"),
    ("/value.html", "ROI 价值分析"),
    ("/assets/phase103_ui.css", "--red"),
    ("/assets/phase103_demo_data.js", "PHASE103_DEMO_DATA"),
)


@dataclass(frozen=True)
class AcceptanceCheck:
    """Single acceptance check result."""

    key: str
    title: str
    passed: bool
    severity: str = "critical"
    detail: str = ""
    suggested_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "passed": self.passed,
            "severity": self.severity,
            "detail": self.detail,
            "suggested_action": self.suggested_action,
        }


@dataclass
class AcceptanceReport:
    """Acceptance summary for one scenario preview bundle."""

    scenario: str
    project_id: str
    version: str = PHASE103W_VERSION
    checks: list[AcceptanceCheck] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(check.passed or check.severity != "critical" for check in self.checks)

    @property
    def score(self) -> int:
        if not self.checks:
            return 0
        passed_count = sum(1 for check in self.checks if check.passed)
        return int(round((passed_count / len(self.checks)) * 100))

    @property
    def failed_checks(self) -> list[AcceptanceCheck]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "scenario": self.scenario,
            "project_id": self.project_id,
            "passed": self.passed,
            "score": self.score,
            "checks": [check.to_dict() for check in self.checks],
            "failed_checks": [check.to_dict() for check in self.failed_checks],
            "artifacts": redact_value(self.artifacts),
        }

    def to_markdown(self) -> str:
        lines = [
            f"# Phase103W 预览验收报告 - {self.scenario}",
            "",
            f"- 版本：{self.version}",
            f"- 项目：{self.project_id}",
            f"- 结论：{'通过' if self.passed else '未通过'}",
            f"- 验收分：{self.score}/100",
            "",
            "## 验收项",
            "",
            "| 状态 | 等级 | 验收项 | 说明 |",
            "| --- | --- | --- | --- |",
        ]
        for check in self.checks:
            status = "通过" if check.passed else "失败"
            lines.append(
                f"| {status} | {check.severity} | {check.title} | {check.detail or '-'} |"
            )
        if self.failed_checks:
            lines.extend(["", "## 建议动作", ""])
            for check in self.failed_checks:
                lines.append(f"- {check.title}：{check.suggested_action or '请检查对应页面/API 输出。'}")
        lines.extend(
            [
                "",
                "## 安全说明",
                "",
                "本验收报告只记录脱敏后的页面/API 校验结果，不包含 token、cookie、password、session 原值或客户敏感数据。",
                "",
            ]
        )
        return "\n".join(lines)


def _json_body(site: Phase103PreviewSite, path: str) -> tuple[bool, dict[str, Any], str]:
    response = site.route(path)
    try:
        payload = response.json_body()
    except Exception as exc:  # pragma: no cover - defensive bad JSON path
        return False, {}, f"{path} 返回非 JSON：{exc}"
    success = response.status == 200 and bool(payload.get("success", False))
    detail = f"HTTP {response.status}"
    if not success and payload.get("error"):
        detail += f"，{payload['error'].get('message')}"
    return success, payload, detail


def _contains_any(text: str, patterns: Iterable[str]) -> list[str]:
    return [pattern for pattern in patterns if pattern and pattern in text]


def _safe_json(payload: Any) -> str:
    return json.dumps(redact_value(payload), ensure_ascii=False, sort_keys=True)


def validate_preview_site(
    *,
    scenario: str = "manufacturing",
    static_dir: str | Path | None = None,
    secret_patterns: Sequence[str] = DEFAULT_SECRET_PATTERNS,
) -> AcceptanceReport:
    """Build a preview site and validate static pages, APIs, and redaction."""
    site = Phase103PreviewSite(scenario=scenario, static_dir=static_dir)
    report = AcceptanceReport(scenario=scenario, project_id=site.project_id)

    # 1) Static pages and required markers.
    static_payloads: list[str] = []
    missing_pages: list[str] = []
    for path, marker in REQUIRED_PAGES:
        response = site.route(path)
        body = response.body.decode("utf-8", errors="replace")
        static_payloads.append(body)
        if response.status != 200 or marker not in body:
            missing_pages.append(f"{path} 缺少标记 {marker!r} 或返回 HTTP {response.status}")
    report.checks.append(
        AcceptanceCheck(
            key="static_pages",
            title="静态页面与设计资产完整",
            passed=not missing_pages,
            detail="全部核心页面可访问" if not missing_pages else "; ".join(missing_pages),
            suggested_action="重新运行 Phase103U/Phase103V 生成静态包，检查页面文件和 assets 是否完整。",
        )
    )

    # 2) Preview manifest and API route envelope.
    manifest_ok, manifest, manifest_detail = _json_body(site, "/api/v1/preview/manifest")
    report.checks.append(
        AcceptanceCheck(
            key="preview_manifest",
            title="预览 manifest 可读取",
            passed=manifest_ok and manifest.get("data", {}).get("project_id") == site.project_id,
            detail=manifest_detail,
            suggested_action="检查 preview server manifest 生成逻辑和 project_id 绑定。",
        )
    )

    base = f"/api/v1/projects/{site.project_id}"
    api_paths = {
        "projects": "/api/v1/projects",
        "project": base,
        "onboarding": f"{base}/onboarding",
        "business_model": f"{base}/business-model",
        "environment": f"{base}/environment/readiness",
        "test_plan": f"{base}/test-plan",
        "command_center": f"{base}/command-center",
        "live_map": f"{base}/live-map",
        "risks": f"{base}/risks",
        "value_metrics": f"{base}/value-metrics",
        "executive_report": f"{base}/reports/executive",
    }
    api_payloads: dict[str, Any] = {}
    failed_routes: list[str] = []
    for key, path in api_paths.items():
        ok, payload, detail = _json_body(site, path)
        api_payloads[key] = payload
        if not ok:
            failed_routes.append(f"{path}: {detail}")
    report.checks.append(
        AcceptanceCheck(
            key="api_routes",
            title="V1 只读 API 路由可用",
            passed=not failed_routes,
            detail="全部核心 API success=true" if not failed_routes else "; ".join(failed_routes),
            suggested_action="检查 Phase103S API façade 和 Phase103V 路由映射是否一致。",
        )
    )

    # 3) Product-critical data quality.
    dashboard = api_payloads.get("command_center", {}).get("data", {})
    top_risks = dashboard.get("top_risks") or []
    launch_decision = dashboard.get("launch_decision") or {}
    business_summary = dashboard.get("business_flow_summary") or {}
    value = api_payloads.get("value_metrics", {}).get("data", {})
    report.checks.append(
        AcceptanceCheck(
            key="dashboard_business_value",
            title="驾驶舱具备领导层决策价值",
            passed=bool(
                dashboard.get("quality_health_score") is not None
                and launch_decision.get("recommendation")
                and business_summary.get("total")
                and top_risks
                and value.get("estimated_hours_saved") is not None
            ),
            detail=f"质量分={dashboard.get('quality_health_score')}，上线建议={launch_decision.get('recommendation')}，Top风险={len(top_risks)}",
            suggested_action="检查 CommandCenterSnapshot 是否聚合 quality_health_score、launch_decision、top_risks、value_metrics。",
        )
    )

    environment = api_payloads.get("environment", {}).get("data", {})
    checks = environment.get("checks") or {}
    report.checks.append(
        AcceptanceCheck(
            key="environment_readiness",
            title="环境适配诊断可解释",
            passed=bool(environment.get("status") and checks.get("http") and checks.get("auth") and checks.get("api_smoke")),
            detail=f"状态={environment.get('status')}，评分={environment.get('score')}，阻断={len(environment.get('current_blockers') or [])}",
            suggested_action="检查 EnvironmentReadinessReport 是否包含 URL/HTTP/Auth/Session/API Smoke 诊断和客户补料清单。",
        )
    )

    live_map = api_payloads.get("live_map", {}).get("data", {})
    report.checks.append(
        AcceptanceCheck(
            key="live_map_risk_overlay",
            title="实时地图能承载风险态势",
            passed=bool(live_map.get("nodes") and live_map.get("edges") and live_map.get("risk_overlays")),
            detail=f"节点={len(live_map.get('nodes') or [])}，链路={len(live_map.get('edges') or [])}，风险层={len(live_map.get('risk_overlays') or [])}",
            suggested_action="检查 BusinessFlow 到 MapNode/MapEdge/RiskOverlay 的转换逻辑。",
        )
    )

    risks = api_payloads.get("risks", {}).get("data") or []
    risk_detail_ok = False
    if risks:
        first_risk_id = risks[0].get("risk_id")
        ok, risk_detail, detail = _json_body(site, f"{base}/risks/{first_risk_id}")
        api_payloads["risk_detail"] = risk_detail
        risk_data = risk_detail.get("data", {}) if ok else {}
        risk_detail_ok = bool(
            ok
            and risk_data.get("risk", {}).get("business_impact")
            and risk_data.get("evidence_bundle", {}).get("redaction_status") == "safe"
            and risk_data.get("evidence_bundle", {}).get("reproduction_steps")
        )
        risk_detail_detail = detail
    else:
        risk_detail_detail = "风险列表为空"
    report.checks.append(
        AcceptanceCheck(
            key="risk_evidence_detail",
            title="风险详情具备业务影响与脱敏证据链",
            passed=risk_detail_ok,
            detail=risk_detail_detail,
            suggested_action="检查 RiskFinding/EvidenceBundle 是否包含 business_impact、redaction_status 和 reproduction_steps。",
        )
    )

    executive_report = api_payloads.get("executive_report", {}).get("data", {})
    report.checks.append(
        AcceptanceCheck(
            key="executive_report",
            title="领导层成果战报可汇报",
            passed=bool(executive_report.get("executive_summary") and executive_report.get("next_actions")),
            detail=f"摘要长度={len(str(executive_report.get('executive_summary') or ''))}，行动项={len(executive_report.get('next_actions') or [])}",
            suggested_action="检查 ExecutiveReportGenerator 是否生成执行摘要、上线建议、Top风险和下一步行动建议。",
        )
    )

    # 4) Redaction across all static and API payloads.
    combined_static = "\n".join(static_payloads)
    combined_api = _safe_json(api_payloads)
    leaked = sorted(set(_contains_any(combined_static + combined_api, secret_patterns)))
    report.checks.append(
        AcceptanceCheck(
            key="redaction_gate",
            title="静态页面与 API 未泄露敏感凭证",
            passed=not leaked,
            detail="未发现禁用敏感片段" if not leaked else f"发现敏感片段：{', '.join(leaked)}",
            suggested_action="检查 redaction path，禁止 token/cookie/password/session/client_secret 原值进入前端与报告。",
        )
    )

    # 5) Safety of unsupported write methods and traversal attempts.
    post = site.route("/api/v1/projects", method="POST")
    traversal = site.route("/../secret.txt")
    report.checks.append(
        AcceptanceCheck(
            key="preview_safety",
            title="预览服务只读且防目录穿越",
            passed=post.status == 405 and traversal.status == 404,
            detail=f"POST={post.status}，traversal={traversal.status}",
            suggested_action="检查 Phase103V 方法限制和静态路径安全校验。",
        )
    )

    report.artifacts = {
        "static_dir": str(site.static_dir),
        "preview_manifest": manifest.get("data", {}),
        "api_routes": api_paths,
        "redaction_status": "safe" if not leaked else "failed",
    }
    return report


def validate_scenarios(
    scenarios: Sequence[str],
    *,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Validate multiple demo scenarios and optionally write reports."""
    root = Path(output_dir) if output_dir is not None else None
    if root is not None:
        root.mkdir(parents=True, exist_ok=True)
    reports: list[AcceptanceReport] = []
    for scenario in scenarios:
        scenario_dir = root / scenario / "site" if root is not None else None
        report = validate_preview_site(scenario=scenario, static_dir=scenario_dir)
        reports.append(report)
        if root is not None:
            scenario_root = root / scenario
            scenario_root.mkdir(parents=True, exist_ok=True)
            (scenario_root / "acceptance_report.json").write_text(
                json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
            )
            (scenario_root / "acceptance_report.md").write_text(report.to_markdown(), encoding="utf-8")
    summary = {
        "version": PHASE103W_VERSION,
        "passed": all(report.passed for report in reports),
        "scenario_count": len(reports),
        "average_score": int(round(sum(report.score for report in reports) / len(reports))) if reports else 0,
        "reports": [report.to_dict() for report in reports],
    }
    if root is not None:
        (root / "acceptance_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        lines = [
            "# Phase103W 多场景预览验收汇总",
            "",
            f"- 版本：{PHASE103W_VERSION}",
            f"- 结论：{'通过' if summary['passed'] else '未通过'}",
            f"- 平均分：{summary['average_score']}/100",
            "",
            "| 场景 | 结论 | 分数 | 失败项 |",
            "| --- | --- | --- | --- |",
        ]
        for report in reports:
            failed = ", ".join(check.key for check in report.failed_checks) or "-"
            lines.append(f"| {report.scenario} | {'通过' if report.passed else '未通过'} | {report.score} | {failed} |")
        (root / "acceptance_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Phase103 local preview pages, APIs, and redaction gates.")
    parser.add_argument(
        "--scenario",
        action="append",
        choices=["manufacturing", "ecommerce", "saas"],
        help="Scenario to validate. Repeat to validate multiple. Defaults to all scenarios.",
    )
    parser.add_argument("--output-dir", default="outputs/phase103_preview_acceptance", help="Directory for acceptance reports.")
    parser.add_argument("--no-write", action="store_true", help="Validate without writing acceptance report files.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    scenarios = tuple(args.scenario or ["manufacturing", "ecommerce", "saas"])
    summary = validate_scenarios(scenarios, output_dir=None if args.no_write else args.output_dir)
    print(json.dumps({"passed": summary["passed"], "average_score": summary["average_score"], "scenario_count": summary["scenario_count"]}, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] else 1


__all__ = [
    "AcceptanceCheck",
    "AcceptanceReport",
    "DEFAULT_SECRET_PATTERNS",
    "PHASE103W_VERSION",
    "validate_preview_site",
    "validate_scenarios",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
