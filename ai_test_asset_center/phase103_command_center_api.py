from __future__ import annotations

"""Phase103S: framework-free V1 Command Center API facade.

This module gives the future Web/API layer a stable protocol without forcing a
specific framework.  It wraps the Phase103 foundation objects in:

* API response envelopes compatible with the V1 PRD.
* A small in-memory project store for local demos, tests, and CLI flows.
* Endpoint-like methods for project onboarding, business modelling,
  environment preflight, test-plan generation, simulated test runs, dashboard,
  live map, risk center, evidence details, value metrics, and executive report.

The facade intentionally keeps business judgement in the backend layer.  UI
clients should consume the already-translated business objects instead of
re-deriving launch decisions, ROI, risk severity, or redaction status.
"""

import copy
import hashlib
import json
import time
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import (
    build_command_center_snapshot,
    build_customer_business_model,
    build_environment_readiness_report,
    build_evidence_bundle,
    build_realtime_map_snapshot,
    calculate_value_metrics,
    generate_ai_test_plan,
    generate_executive_report,
    get_industry_template,
    list_industry_templates,
    redact_value,
    translate_risk_finding,
)

PHASE103S_VERSION = "phase103s-command-center-api-v1"


class CommandCenterAPIError(Exception):
    """Structured API error that can be rendered in the shared envelope."""

    def __init__(self, code: str, message: str, *, status: int = 400, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = dict(details or {})


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stable_id(value: Any, prefix: str, size: int = 10) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()[:size]}"


def _deepcopy(value: Any) -> Any:
    return copy.deepcopy(value)


def _safe_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def api_response(data: Any, *, request_id: str | None = None, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return a V1 success response envelope."""
    return {
        "success": True,
        "data": redact_value(_deepcopy(data)),
        "error": None,
        "meta": {
            "request_id": request_id or _stable_id(["req", data, _now()], "req", 12),
            "generated_at": _now(),
            "version": PHASE103S_VERSION,
            **dict(meta or {}),
        },
    }


def api_error_response(error: Exception, *, request_id: str | None = None) -> dict[str, Any]:
    """Return a V1 error response envelope with customer-safe details."""
    if isinstance(error, CommandCenterAPIError):
        code = error.code
        message = error.message
        status = error.status
        details = error.details
    else:
        code = "INTERNAL_ERROR"
        message = "系统处理请求时发生异常，请稍后重试或联系项目负责人。"
        status = 500
        details = {"exception_type": type(error).__name__}
    return {
        "success": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "status": status,
            "details": redact_value(details),
        },
        "meta": {
            "request_id": request_id or _stable_id(["err", code, message, _now()], "req", 12),
            "generated_at": _now(),
            "version": PHASE103S_VERSION,
        },
    }


class EnterpriseCommandCenterAPI:
    """In-memory V1 API facade for Phase103 enterprise command-center flows."""

    def __init__(self) -> None:
        self.projects: dict[str, dict[str, Any]] = {}
        self.onboarding: dict[str, dict[str, Any]] = {}
        self.business_models: dict[str, dict[str, Any]] = {}
        self.environment_configs: dict[str, dict[str, Any]] = {}
        self.environment_reports: dict[str, dict[str, Any]] = {}
        self.test_plans: dict[str, dict[str, Any]] = {}
        self.test_runs: dict[str, dict[str, Any]] = {}
        self.risks: dict[str, list[dict[str, Any]]] = {}
        self.evidence: dict[str, dict[str, Any]] = {}
        self.reports: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------
    def ok(self, data: Any, *, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return api_response(data, meta=meta)

    def fail(self, error: Exception) -> dict[str, Any]:
        return api_error_response(error)

    # ------------------------------------------------------------------
    # Project + onboarding
    # ------------------------------------------------------------------
    def create_project(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = _safe_mapping(payload)
        project_name = str(body.get("project_name") or body.get("name") or "").strip()
        customer_name = str(body.get("customer_name") or "").strip()
        system_name = str(body.get("system_name") or project_name or "").strip()
        if not project_name:
            raise CommandCenterAPIError("PROJECT_NAME_REQUIRED", "项目名称不能为空。", details={"field": "project_name"})
        project_id = str(body.get("project_id") or _stable_id([customer_name, project_name, system_name], "proj", 10))
        industry = str(body.get("industry") or "").strip().lower()
        project = {
            "project_id": project_id,
            "customer_name": customer_name,
            "project_name": project_name,
            "system_name": system_name,
            "industry": industry or None,
            "system_type": body.get("system_type"),
            "test_goal": body.get("test_goal") or "上线前核心业务链路质量风险评估",
            "planned_launch_date": body.get("planned_launch_date"),
            "status": "onboarding",
            "owner": body.get("owner"),
            "created_at": _now(),
            "updated_at": _now(),
            "version": PHASE103S_VERSION,
        }
        self.projects[project_id] = redact_value(project)
        self.onboarding[project_id] = self._build_onboarding(project_id, current_step="business_model")
        return self.ok(project)

    def list_projects(self) -> dict[str, Any]:
        return self.ok(sorted(self.projects.values(), key=lambda item: str(item.get("created_at", ""))))

    def get_project(self, project_id: str) -> dict[str, Any]:
        return self.ok(self._require_project(project_id))

    def get_onboarding(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        self.onboarding[project_id] = self._build_onboarding(project_id)
        return self.ok(self.onboarding[project_id])

    # ------------------------------------------------------------------
    # Industry templates + business model
    # ------------------------------------------------------------------
    def list_industry_templates(self) -> dict[str, Any]:
        return self.ok(list_industry_templates())

    def apply_business_template(self, project_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        project = self._require_project(project_id)
        body = _safe_mapping(payload)
        template_id = str(body.get("template_id") or body.get("industry") or project.get("industry") or "").strip()
        if not template_id:
            raise CommandCenterAPIError("INDUSTRY_TEMPLATE_REQUIRED", "请选择行业模板后再生成业务链路。")
        template = get_industry_template(template_id)
        enabled_flow_ids = body.get("enabled_flow_ids")
        critical_flow_ids = body.get("critical_flow_ids")
        role_config = _safe_mapping(body.get("role_config"))
        model = build_customer_business_model(
            project_id,
            template["industry"],
            enabled_flow_ids=enabled_flow_ids,
            critical_flow_ids=critical_flow_ids,
            role_config=role_config,
            custom_terms=_safe_mapping(body.get("custom_terms")),
            approved_by=body.get("approved_by"),
        )
        self.business_models[project_id] = model
        project["industry"] = template["industry"]
        project["status"] = "environment_pending"
        project["updated_at"] = _now()
        self.onboarding[project_id] = self._build_onboarding(project_id, current_step="environment")
        return self.ok(model)

    def get_business_model(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        model = self.business_models.get(project_id)
        if not model:
            raise CommandCenterAPIError("BUSINESS_MODEL_NOT_FOUND", "当前项目尚未完成业务链路建模。", details={"next_step": "apply_template"})
        return self.ok(model)

    def patch_business_model(self, project_id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        model = _deepcopy(self._require_business_model(project_id))
        body = _safe_mapping(patch)
        if "confirmed_business_flows" in body and isinstance(body["confirmed_business_flows"], list):
            model["confirmed_business_flows"] = body["confirmed_business_flows"]
        if "confirmed_roles" in body and isinstance(body["confirmed_roles"], list):
            model["confirmed_roles"] = body["confirmed_roles"]
        if "confirmed_risk_focus" in body and isinstance(body["confirmed_risk_focus"], list):
            model["confirmed_risk_focus"] = body["confirmed_risk_focus"]
        if "approved_by" in body:
            model["approved_by"] = body.get("approved_by")
            model["approved_at"] = _now() if body.get("approved_by") else None
        model["updated_at"] = _now()
        self.business_models[project_id] = redact_value(model)
        return self.ok(model)

    # ------------------------------------------------------------------
    # Environment readiness
    # ------------------------------------------------------------------
    def patch_environment_config(self, project_id: str, config: Mapping[str, Any]) -> dict[str, Any]:
        self._require_project(project_id)
        safe_config = redact_value(_safe_mapping(config))
        self.environment_configs[project_id] = safe_config
        return self.ok({"project_id": project_id, "status": "saved", "config": safe_config})

    def run_environment_preflight(self, project_id: str, preflight_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._require_project(project_id)
        business_model = self.business_models.get(project_id)
        merged = _deepcopy(self.environment_configs.get(project_id, {}))
        if preflight_result:
            merged.update(_safe_mapping(preflight_result))
        report = build_environment_readiness_report(project_id, merged, business_model)
        self.environment_reports[project_id] = report
        project = self.projects[project_id]
        project["status"] = "ready_for_test" if report.get("status") in {"ready", "partial_ready", "needs_customer_input"} else "environment_pending"
        project["updated_at"] = _now()
        self.onboarding[project_id] = self._build_onboarding(project_id, current_step="test_plan")
        return self.ok(report)

    def get_environment_readiness(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        report = self.environment_reports.get(project_id)
        if not report:
            report = build_environment_readiness_report(project_id, self.environment_configs.get(project_id, {}), self.business_models.get(project_id))
            self.environment_reports[project_id] = report
        return self.ok(report)

    # ------------------------------------------------------------------
    # Test plan + run
    # ------------------------------------------------------------------
    def generate_test_plan(self, project_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        model = self._require_business_model(project_id)
        env = self.environment_reports.get(project_id)
        if not env:
            raise CommandCenterAPIError("ENVIRONMENT_PREFLIGHT_REQUIRED", "请先完成客户环境适配预检，再生成 AI 测试计划。")
        plan_name = _safe_mapping(payload).get("plan_name") if payload else None
        plan = generate_ai_test_plan(project_id, model, env, plan_name=plan_name)
        self.test_plans[project_id] = plan
        self.onboarding[project_id] = self._build_onboarding(project_id, current_step="run_test")
        return self.ok(plan)

    def get_test_plan(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        plan = self.test_plans.get(project_id)
        if not plan:
            raise CommandCenterAPIError("TEST_PLAN_NOT_FOUND", "当前项目尚未生成 AI 测试计划。", details={"next_step": "generate_test_plan"})
        return self.ok(plan)

    def start_test_run(self, project_id: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        project = self._require_project(project_id)
        plan = self.test_plans.get(project_id)
        if not plan:
            raise CommandCenterAPIError("TEST_PLAN_REQUIRED", "请先生成 AI 测试计划，再启动测试。")
        body = _safe_mapping(payload)
        raw_findings = body.get("findings") if isinstance(body.get("findings"), list) else []
        risks: list[dict[str, Any]] = []
        evidence_items: dict[str, dict[str, Any]] = {}
        for finding in raw_findings:
            if not isinstance(finding, Mapping):
                continue
            risk = translate_risk_finding(project_id, finding, self.business_models.get(project_id))
            ev = build_evidence_bundle(risk, finding.get("evidence") if isinstance(finding.get("evidence"), Mapping) else finding)
            risks.append(risk)
            evidence_items[str(risk["risk_id"])] = ev
        run_id = str(body.get("run_id") or _stable_id([project_id, plan.get("plan_id"), len(self.test_runs), raw_findings], "run", 10))
        run = {
            "run_id": run_id,
            "project_id": project_id,
            "plan_id": plan.get("plan_id"),
            "status": "completed",
            "started_at": _now(),
            "finished_at": _now(),
            "progress": 1.0,
            "probe_total": sum(int(group.get("probe_total", 0)) for group in plan.get("probe_groups", [])),
            "probe_completed": sum(int(group.get("probe_executable", 0)) for group in plan.get("probe_groups", [])),
            "probe_failed": len(risks),
            "risk_found": len(risks),
        }
        self.test_runs[run_id] = run
        self.risks[project_id] = risks
        self.evidence.update(evidence_items)
        project["status"] = "tested"
        project["updated_at"] = _now()
        return self.ok({"test_run": run, "risks": risks, "evidence_count": len(evidence_items)})

    def get_test_run(self, project_id: str, run_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        run = self.test_runs.get(run_id)
        if not run or run.get("project_id") != project_id:
            raise CommandCenterAPIError("TEST_RUN_NOT_FOUND", "未找到指定测试运行。", status=404)
        return self.ok(run)

    # ------------------------------------------------------------------
    # Dashboard + map + risks + value + report
    # ------------------------------------------------------------------
    def get_command_center(self, project_id: str) -> dict[str, Any]:
        project = self._require_project(project_id)
        model = self._require_business_model(project_id)
        env = self.environment_reports.get(project_id) or build_environment_readiness_report(project_id, {}, model)
        plan = self.test_plans.get(project_id)
        risks = self.risks.get(project_id, [])
        evidence = [self.evidence[str(r["risk_id"])] for r in risks if str(r.get("risk_id")) in self.evidence]
        latest_run = self._latest_run_for_project(project_id)
        snapshot = build_command_center_snapshot(project, model, env, plan, risks, evidence, latest_run)
        return self.ok(snapshot)

    def get_live_map(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        model = self._require_business_model(project_id)
        latest_run = self._latest_run_for_project(project_id)
        live_map = build_realtime_map_snapshot(project_id, model, self.risks.get(project_id, []), latest_run)
        return self.ok(live_map)

    def list_risks(self, project_id: str, filters: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._require_project(project_id)
        filters = _safe_mapping(filters)
        items = list(self.risks.get(project_id, []))
        if filters.get("severity"):
            items = [risk for risk in items if risk.get("severity") == filters["severity"]]
        if filters.get("launch_blocking") is not None:
            expected = bool(filters["launch_blocking"])
            items = [risk for risk in items if bool(risk.get("launch_blocking")) is expected]
        if filters.get("business_flow_id"):
            items = [risk for risk in items if _safe_mapping(risk.get("affected_business_flow")).get("business_flow_id") == filters["business_flow_id"]]
        if filters.get("status"):
            items = [risk for risk in items if risk.get("status") == filters["status"]]
        return self.ok(items, meta={"total": len(items)})

    def get_risk_detail(self, project_id: str, risk_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        risk = next((item for item in self.risks.get(project_id, []) if item.get("risk_id") == risk_id), None)
        if not risk:
            raise CommandCenterAPIError("RISK_NOT_FOUND", "未找到指定风险。", status=404)
        return self.ok({"risk": risk, "evidence_bundle": self.evidence.get(risk_id)})

    def get_value_metrics(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        model = self._require_business_model(project_id)
        flow_summary = build_command_center_snapshot(
            self.projects[project_id],
            model,
            self.environment_reports.get(project_id) or build_environment_readiness_report(project_id, {}, model),
            self.test_plans.get(project_id),
            self.risks.get(project_id, []),
            list(self.evidence.values()),
            self._latest_run_for_project(project_id),
        )["business_flow_summary"]
        metrics = calculate_value_metrics(project_id, self.test_plans.get(project_id), self.risks.get(project_id, []), list(self.evidence.values()), flow_summary)
        return self.ok(metrics)

    def generate_report(self, project_id: str) -> dict[str, Any]:
        snapshot = self.get_command_center(project_id)["data"]
        risks = self.risks.get(project_id, [])
        evidence = [self.evidence[str(r["risk_id"])] for r in risks if str(r.get("risk_id")) in self.evidence]
        report = generate_executive_report(self.projects[project_id], snapshot, risks, evidence)
        self.reports[project_id] = report
        self.projects[project_id]["status"] = "report_generated"
        return self.ok(report)

    def get_report(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        report = self.reports.get(project_id)
        if not report:
            raise CommandCenterAPIError("REPORT_NOT_FOUND", "当前项目尚未生成领导层成果战报。", details={"next_step": "generate_report"})
        return self.ok(report)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _require_project(self, project_id: str) -> dict[str, Any]:
        project = self.projects.get(project_id)
        if not project:
            raise CommandCenterAPIError("PROJECT_NOT_FOUND", "未找到指定项目。", status=404, details={"project_id": project_id})
        return project

    def _require_business_model(self, project_id: str) -> dict[str, Any]:
        self._require_project(project_id)
        model = self.business_models.get(project_id)
        if not model:
            raise CommandCenterAPIError("BUSINESS_MODEL_REQUIRED", "请先完成业务链路建模。", details={"next_step": "business_model"})
        return model

    def _latest_run_for_project(self, project_id: str) -> dict[str, Any] | None:
        runs = [run for run in self.test_runs.values() if run.get("project_id") == project_id]
        return sorted(runs, key=lambda item: str(item.get("started_at", "")))[-1] if runs else None

    def _build_onboarding(self, project_id: str, *, current_step: str | None = None) -> dict[str, Any]:
        has_project = project_id in self.projects
        has_model = project_id in self.business_models
        has_env = project_id in self.environment_reports
        has_plan = project_id in self.test_plans
        has_run = any(run.get("project_id") == project_id for run in self.test_runs.values())
        if current_step is None:
            if not has_model:
                current_step = "business_model"
            elif not has_env:
                current_step = "environment"
            elif not has_plan:
                current_step = "test_plan"
            elif not has_run:
                current_step = "run_test"
            else:
                current_step = "completed"
        raw_steps = [
            ("project_created", "创建项目", has_project),
            ("business_model", "业务链路建模", has_model),
            ("environment", "环境适配", has_env),
            ("test_plan", "AI 测试计划", has_plan),
            ("run_test", "启动测试", has_run),
        ]
        steps = []
        for key, label, done in raw_steps:
            status = "completed" if done else "in_progress" if key == current_step else "pending"
            steps.append({"key": key, "label": label, "status": status})
        completed = sum(1 for _, _, done in raw_steps if done)
        return {
            "project_id": project_id,
            "current_step": current_step,
            "steps": steps,
            "completion_rate": round(completed / len(raw_steps), 2),
        }


__all__ = [
    "CommandCenterAPIError",
    "EnterpriseCommandCenterAPI",
    "PHASE103S_VERSION",
    "api_error_response",
    "api_response",
]
