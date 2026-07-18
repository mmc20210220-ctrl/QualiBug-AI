from __future__ import annotations

"""Phase104B: OpenAPI contract and frontend integration kit exporter.

Phase104A introduced a mutation-capable local HTTP API for the Enterprise
Command Center. This module exports that route contract as stable developer
artifacts so a real frontend can integrate without reading backend source code:

* ``openapi.json`` for API tools and frontend generators;
* ``API_CONTRACT.md`` for human-readable route handoff;
* ``frontend_api_client.ts`` for a small fetch-based TypeScript client;
* ``contract_manifest.json`` for CI/acceptance gates.

The exporter is stdlib-only and does not start the HTTP server. All examples use
placeholder values and deliberately avoid raw credential examples.
"""

import argparse
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from ai_test_asset_center.phase103_enterprise_command_center import redact_value
from ai_test_asset_center.phase104_command_center_http_api import PHASE104A_VERSION

PHASE104B_VERSION = "phase104b-api-contract-exporter-v1"

SENSITIVE_EXAMPLE_PATTERNS = (
    "raw-token",
    "raw-cookie",
    "raw-session",
    "raw-password",
    "client_secret=",
    "password=",
    "SESSION=raw",
    "Bearer raw",
)


@dataclass(frozen=True)
class RouteContract:
    method: str
    path: str
    operation_id: str
    summary: str
    tag: str
    description: str = ""
    request_schema: str | None = None
    response_schema: str = "ApiEnvelope"
    query_params: list[dict[str, Any]] = field(default_factory=list)
    status: int = 200


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _schema_ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}"}


def _json_response(description: str, schema_name: str = "ApiEnvelope") -> dict[str, Any]:
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": _schema_ref(schema_name),
            }
        },
    }


def _request_body(schema_name: str) -> dict[str, Any]:
    return {
        "required": True,
        "content": {
            "application/json": {
                "schema": _schema_ref(schema_name),
            }
        },
    }


def route_contracts() -> list[RouteContract]:
    """Return the V1 HTTP route contract in the same order as the PRD flow."""
    return [
        RouteContract("GET", "/api/v1/health", "getHealth", "健康检查", "System"),
        RouteContract("GET", "/api/v1/industry-templates", "listIndustryTemplates", "查询行业模板", "Industry"),
        RouteContract("GET", "/api/v1/projects", "listProjects", "查询项目列表", "Project"),
        RouteContract("POST", "/api/v1/projects", "createProject", "创建项目", "Project", request_schema="ProjectCreateRequest", status=201),
        RouteContract("GET", "/api/v1/projects/{project_id}", "getProject", "查询项目详情", "Project"),
        RouteContract("GET", "/api/v1/projects/{project_id}/onboarding", "getOnboarding", "查询初始化进度", "Project"),
        RouteContract("GET", "/api/v1/projects/{project_id}/business-model", "getBusinessModel", "查询业务链路模型", "BusinessModel"),
        RouteContract("PATCH", "/api/v1/projects/{project_id}/business-model", "patchBusinessModel", "保存业务链路模型", "BusinessModel", request_schema="BusinessModelPatchRequest"),
        RouteContract("POST", "/api/v1/projects/{project_id}/business-model/apply-template", "applyBusinessTemplate", "应用行业模板", "BusinessModel", request_schema="ApplyTemplateRequest"),
        RouteContract("GET", "/api/v1/projects/{project_id}/environment/readiness", "getEnvironmentReadiness", "查询环境适配结果", "Environment"),
        RouteContract("PATCH", "/api/v1/projects/{project_id}/environment/config", "patchEnvironmentConfig", "保存环境配置", "Environment", request_schema="EnvironmentConfigPatchRequest"),
        RouteContract("POST", "/api/v1/projects/{project_id}/environment/preflight", "runEnvironmentPreflight", "执行环境预检", "Environment", request_schema="EnvironmentPreflightRequest"),
        RouteContract("GET", "/api/v1/projects/{project_id}/test-plan", "getTestPlan", "查询 AI 测试计划", "TestPlan"),
        RouteContract("POST", "/api/v1/projects/{project_id}/test-plan/generate", "generateTestPlan", "生成 AI 测试计划", "TestPlan", request_schema="TestPlanGenerateRequest"),
        RouteContract("POST", "/api/v1/projects/{project_id}/test-runs", "startTestRun", "启动测试运行", "TestRun", request_schema="TestRunStartRequest", status=201),
        RouteContract("GET", "/api/v1/projects/{project_id}/test-runs/{run_id}", "getTestRun", "查询测试运行", "TestRun"),
        RouteContract("GET", "/api/v1/projects/{project_id}/command-center", "getCommandCenter", "查询质量驾驶舱", "CommandCenter"),
        RouteContract("GET", "/api/v1/projects/{project_id}/live-map", "getLiveMap", "查询实时测试地图", "LiveMap"),
        RouteContract(
            "GET",
            "/api/v1/projects/{project_id}/risks",
            "listRisks",
            "查询 AI 风险列表",
            "Risk",
            query_params=[
                {"name": "severity", "schema": {"type": "string"}},
                {"name": "business_flow_id", "schema": {"type": "string"}},
                {"name": "status", "schema": {"type": "string"}},
                {"name": "launch_blocking", "schema": {"type": "boolean"}},
            ],
        ),
        RouteContract("GET", "/api/v1/projects/{project_id}/risks/{risk_id}", "getRiskDetail", "查询风险证据链详情", "Risk"),
        RouteContract("GET", "/api/v1/projects/{project_id}/value-metrics", "getValueMetrics", "查询 ROI 价值指标", "Value"),
        RouteContract("GET", "/api/v1/projects/{project_id}/reports/executive", "getExecutiveReport", "查询领导成果战报", "Report"),
        RouteContract("POST", "/api/v1/projects/{project_id}/reports/generate", "generateExecutiveReport", "生成领导成果战报", "Report"),
    ]


def _components() -> dict[str, Any]:
    string = {"type": "string"}
    return {
        "schemas": {
            "ApiEnvelope": {
                "type": "object",
                "required": ["success", "data", "error", "meta"],
                "properties": {
                    "success": {"type": "boolean"},
                    "data": {"description": "业务数据对象。具体结构以对应页面对象为准。"},
                    "error": {"oneOf": [_schema_ref("ApiError"), {"type": "null"}]},
                    "meta": _schema_ref("ApiMeta"),
                },
            },
            "ApiError": {
                "type": "object",
                "properties": {
                    "code": string,
                    "message": string,
                    "status": {"type": "integer"},
                    "details": {"type": "object", "additionalProperties": True},
                },
            },
            "ApiMeta": {
                "type": "object",
                "properties": {
                    "request_id": string,
                    "generated_at": string,
                    "version": string,
                },
            },
            "ProjectCreateRequest": {
                "type": "object",
                "required": ["project_name", "customer_name", "system_name", "industry"],
                "properties": {
                    "project_name": string,
                    "customer_name": string,
                    "system_name": string,
                    "industry": {"type": "string", "enum": ["manufacturing", "ecommerce", "saas", "finance", "healthcare", "government", "education", "logistics"]},
                    "system_type": string,
                    "test_goal": string,
                    "planned_launch_date": string,
                    "owner": string,
                },
            },
            "ApplyTemplateRequest": {
                "type": "object",
                "required": ["template_id"],
                "properties": {
                    "template_id": string,
                    "approved_by": string,
                    "role_config": {"type": "object", "additionalProperties": {"type": "boolean"}},
                },
            },
            "BusinessModelPatchRequest": {
                "type": "object",
                "properties": {
                    "confirmed_business_flows": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                    "confirmed_roles": {"type": "array", "items": string},
                    "confirmed_risk_focus": {"type": "array", "items": string},
                    "approved_by": string,
                },
            },
            "EnvironmentConfigPatchRequest": {
                "type": "object",
                "properties": {
                    "base_url": string,
                    "auth_type": string,
                    "safe_execution_mode": string,
                    "session_health_path": string,
                    "api_smoke_paths": {"type": "array", "items": string},
                    "credential_status": {"description": "仅提交凭证状态或脱敏占位，不应在日志/报告中展示原值。", "type": "object", "additionalProperties": True},
                },
            },
            "EnvironmentPreflightRequest": {
                "type": "object",
                "properties": {
                    "safe_execution_mode": string,
                    "checks": {"type": "object", "additionalProperties": True},
                },
            },
            "TestPlanGenerateRequest": {
                "type": "object",
                "properties": {
                    "plan_name": string,
                    "safe_execution_mode": string,
                },
            },
            "TestRunStartRequest": {
                "type": "object",
                "properties": {
                    "run_id": string,
                    "findings": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
                },
            },
        },
        "parameters": {
            "ProjectId": {"name": "project_id", "in": "path", "required": True, "schema": string},
            "RunId": {"name": "run_id", "in": "path", "required": True, "schema": string},
            "RiskId": {"name": "risk_id", "in": "path", "required": True, "schema": string},
        },
    }


def build_openapi_spec(*, title: str = "QualiBug Enterprise Command Center API") -> dict[str, Any]:
    """Build an OpenAPI 3 contract for the Phase104 local HTTP API."""
    paths: dict[str, Any] = {}
    for route in route_contracts():
        op: dict[str, Any] = {
            "operationId": route.operation_id,
            "summary": route.summary,
            "description": route.description or route.summary,
            "tags": [route.tag],
            "responses": {
                str(route.status): _json_response("成功响应", route.response_schema),
                "400": _json_response("请求格式错误", "ApiEnvelope"),
                "404": _json_response("资源不存在", "ApiEnvelope"),
                "405": _json_response("方法不允许", "ApiEnvelope"),
                "500": _json_response("服务端安全错误响应", "ApiEnvelope"),
            },
        }
        params: list[dict[str, Any]] = []
        if "{project_id}" in route.path:
            params.append(_schema_ref("ParameterProjectId"))
        if "{run_id}" in route.path:
            params.append(_schema_ref("ParameterRunId"))
        if "{risk_id}" in route.path:
            params.append(_schema_ref("ParameterRiskId"))
        params.extend({"name": item["name"], "in": "query", "required": False, "schema": item["schema"]} for item in route.query_params)
        if params:
            # OpenAPI parameters cannot $ref via schemas, use components/parameters references for path params.
            fixed_params: list[dict[str, Any]] = []
            for param in params:
                if param == _schema_ref("ParameterProjectId"):
                    fixed_params.append({"$ref": "#/components/parameters/ProjectId"})
                elif param == _schema_ref("ParameterRunId"):
                    fixed_params.append({"$ref": "#/components/parameters/RunId"})
                elif param == _schema_ref("ParameterRiskId"):
                    fixed_params.append({"$ref": "#/components/parameters/RiskId"})
                else:
                    fixed_params.append(param)
            op["parameters"] = fixed_params
        if route.request_schema:
            op["requestBody"] = _request_body(route.request_schema)
        paths.setdefault(route.path, {})[route.method.lower()] = op

    return {
        "openapi": "3.0.3",
        "info": {
            "title": title,
            "version": PHASE104B_VERSION,
            "description": "QualiBug Phase104 Enterprise Quality Command Center local API contract.",
        },
        "servers": [{"url": "http://127.0.0.1:8088", "description": "Local Phase104A API server"}],
        "paths": paths,
        "components": _components(),
        "x-qualibug": {
            "contract_version": PHASE104B_VERSION,
            "runtime_version": PHASE104A_VERSION,
            "redaction_status": "safe",
            "generated_at": _now(),
        },
    }


def render_contract_markdown(spec: Mapping[str, Any] | None = None) -> str:
    spec = dict(spec or build_openapi_spec())
    lines = [
        "# QualiBug Phase104B API 合同",
        "",
        f"- Contract Version: `{PHASE104B_VERSION}`",
        f"- Runtime Version: `{PHASE104A_VERSION}`",
        "- Base URL: `http://127.0.0.1:8088`",
        "- 安全约束：所有响应默认脱敏，前端不得展示 token/cookie/session/client_secret 原值。",
        "",
        "## 路由总览",
        "",
        "| Method | Path | Operation | 用途 |",
        "|---|---|---|---|",
    ]
    for route in route_contracts():
        lines.append(f"| `{route.method}` | `{route.path}` | `{route.operation_id}` | {route.summary} |")
    lines.extend(
        [
            "",
            "## 前端集成顺序",
            "",
            "1. `POST /api/v1/projects` 创建项目。",
            "2. `POST /business-model/apply-template` 应用行业模板。",
            "3. `PATCH /environment/config` 保存环境配置。",
            "4. `POST /environment/preflight` 执行环境预检。",
            "5. `POST /test-plan/generate` 生成 AI 测试计划。",
            "6. `POST /test-runs` 启动测试运行。",
            "7. `GET /command-center`、`GET /live-map`、`GET /risks`、`GET /reports/executive` 渲染 V1 页面。",
            "",
            "## 错误响应规范",
            "",
            "所有错误响应保持统一 envelope：`success=false`、`data=null`、`error.code`、`error.message`、`meta.generated_at`。错误文案面向客户解释，不暴露 Python traceback。",
            "",
            "## 脱敏规范",
            "",
            "合同示例只包含脱敏占位和状态字段，不包含原始凭证。Credential 类字段只允许进入本地运行时，不能进入报告、静态前端或交付包。",
            "",
        ]
    )
    return "\n".join(lines)


def render_frontend_api_client() -> str:
    """Render a small fetch-based TypeScript client for frontend integration."""
    return """// Auto-generated by QualiBug Phase104B API contract exporter.
// Safe for frontend integration. Do not log request bodies containing credentials.

export type ApiEnvelope<T = unknown> = {
  success: boolean;
  data: T | null;
  error: null | { code: string; message: string; status?: number; details?: Record<string, unknown> };
  meta?: Record<string, unknown>;
};

export class QualiBugCommandCenterClient {
  constructor(private readonly baseUrl: string = 'http://127.0.0.1:8088') {}

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const envelope = (await response.json()) as ApiEnvelope<T>;
    if (!envelope.success) {
      throw new Error(envelope.error?.message || `QualiBug API error: ${response.status}`);
    }
    return envelope.data as T;
  }

  health() { return this.request('GET', '/api/v1/health'); }
  listIndustryTemplates() { return this.request('GET', '/api/v1/industry-templates'); }
  listProjects() { return this.request('GET', '/api/v1/projects'); }
  createProject(payload: Record<string, unknown>) { return this.request('POST', '/api/v1/projects', payload); }
  getProject(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}`); }
  getOnboarding(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}/onboarding`); }
  applyBusinessTemplate(projectId: string, payload: Record<string, unknown>) { return this.request('POST', `/api/v1/projects/${projectId}/business-model/apply-template`, payload); }
  getBusinessModel(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}/business-model`); }
  patchBusinessModel(projectId: string, payload: Record<string, unknown>) { return this.request('PATCH', `/api/v1/projects/${projectId}/business-model`, payload); }
  patchEnvironmentConfig(projectId: string, payload: Record<string, unknown>) { return this.request('PATCH', `/api/v1/projects/${projectId}/environment/config`, payload); }
  runEnvironmentPreflight(projectId: string, payload: Record<string, unknown>) { return this.request('POST', `/api/v1/projects/${projectId}/environment/preflight`, payload); }
  getEnvironmentReadiness(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}/environment/readiness`); }
  generateTestPlan(projectId: string, payload: Record<string, unknown> = {}) { return this.request('POST', `/api/v1/projects/${projectId}/test-plan/generate`, payload); }
  getTestPlan(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}/test-plan`); }
  startTestRun(projectId: string, payload: Record<string, unknown>) { return this.request('POST', `/api/v1/projects/${projectId}/test-runs`, payload); }
  getTestRun(projectId: string, runId: string) { return this.request('GET', `/api/v1/projects/${projectId}/test-runs/${runId}`); }
  getCommandCenter(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}/command-center`); }
  getLiveMap(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}/live-map`); }
  listRisks(projectId: string, query: string = '') { return this.request('GET', `/api/v1/projects/${projectId}/risks${query}`); }
  getRiskDetail(projectId: string, riskId: string) { return this.request('GET', `/api/v1/projects/${projectId}/risks/${riskId}`); }
  getValueMetrics(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}/value-metrics`); }
  getExecutiveReport(projectId: string) { return this.request('GET', `/api/v1/projects/${projectId}/reports/executive`); }
  generateExecutiveReport(projectId: string) { return this.request('POST', `/api/v1/projects/${projectId}/reports/generate`, {}); }
}
"""


def assert_contract_safe(texts: Sequence[str]) -> None:
    combined = "\n".join(texts)
    leaked = [pattern for pattern in SENSITIVE_EXAMPLE_PATTERNS if pattern in combined]
    if leaked:
        raise ValueError(f"API contract contains unsafe example patterns: {', '.join(leaked)}")


def export_api_contract(output_dir: str | Path) -> dict[str, Any]:
    """Export OpenAPI, markdown, TS client, and manifest files."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    spec = build_openapi_spec()
    openapi_text = json.dumps(spec, ensure_ascii=False, indent=2, sort_keys=True)
    markdown_text = render_contract_markdown(spec)
    client_text = render_frontend_api_client()
    assert_contract_safe([openapi_text, markdown_text, client_text])

    files = {
        "openapi.json": openapi_text,
        "API_CONTRACT.md": markdown_text,
        "frontend_api_client.ts": client_text,
    }
    for name, content in files.items():
        (out / name).write_text(content, encoding="utf-8")

    manifest = {
        "version": PHASE104B_VERSION,
        "runtime_version": PHASE104A_VERSION,
        "generated_at": _now(),
        "route_count": len(route_contracts()),
        "artifacts": [
            {"path": name, "sha256": _sha256_text(content), "bytes": len(content.encode("utf-8"))}
            for name, content in files.items()
        ],
        "redaction_status": "safe",
    }
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
    (out / "contract_manifest.json").write_text(manifest_text, encoding="utf-8")
    return manifest


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export Phase104B OpenAPI and frontend integration artifacts.")
    parser.add_argument("--output-dir", default="outputs/phase104_api_contract", help="Output directory for contract artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    manifest = export_api_contract(args.output_dir)
    print(f"Exported Phase104B API contract to {args.output_dir}")
    print(f"Routes: {manifest['route_count']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "PHASE104B_VERSION",
    "RouteContract",
    "assert_contract_safe",
    "build_openapi_spec",
    "export_api_contract",
    "render_contract_markdown",
    "render_frontend_api_client",
    "route_contracts",
    "main",
]

