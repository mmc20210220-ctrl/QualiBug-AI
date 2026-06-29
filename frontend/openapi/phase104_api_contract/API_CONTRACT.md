# QualiBug Phase104B API 合同

- Contract Version: `phase104b-api-contract-exporter-v1`
- Runtime Version: `phase104a-command-center-http-api-v1`
- Base URL: `http://127.0.0.1:8790`
- 安全约束：所有响应默认脱敏，前端不得展示 token/cookie/session/client_secret 原值。

## 路由总览

| Method | Path | Operation | 用途 |
|---|---|---|---|
| `GET` | `/api/v1/health` | `getHealth` | 健康检查 |
| `GET` | `/api/v1/industry-templates` | `listIndustryTemplates` | 查询行业模板 |
| `GET` | `/api/v1/projects` | `listProjects` | 查询项目列表 |
| `POST` | `/api/v1/projects` | `createProject` | 创建项目 |
| `GET` | `/api/v1/projects/{project_id}` | `getProject` | 查询项目详情 |
| `GET` | `/api/v1/projects/{project_id}/onboarding` | `getOnboarding` | 查询初始化进度 |
| `GET` | `/api/v1/projects/{project_id}/business-model` | `getBusinessModel` | 查询业务链路模型 |
| `PATCH` | `/api/v1/projects/{project_id}/business-model` | `patchBusinessModel` | 保存业务链路模型 |
| `POST` | `/api/v1/projects/{project_id}/business-model/apply-template` | `applyBusinessTemplate` | 应用行业模板 |
| `GET` | `/api/v1/projects/{project_id}/environment/readiness` | `getEnvironmentReadiness` | 查询环境适配结果 |
| `PATCH` | `/api/v1/projects/{project_id}/environment/config` | `patchEnvironmentConfig` | 保存环境配置 |
| `POST` | `/api/v1/projects/{project_id}/environment/preflight` | `runEnvironmentPreflight` | 执行环境预检 |
| `GET` | `/api/v1/projects/{project_id}/test-plan` | `getTestPlan` | 查询 AI 测试计划 |
| `POST` | `/api/v1/projects/{project_id}/test-plan/generate` | `generateTestPlan` | 生成 AI 测试计划 |
| `POST` | `/api/v1/projects/{project_id}/test-runs` | `startTestRun` | 启动测试运行 |
| `GET` | `/api/v1/projects/{project_id}/test-runs/{run_id}` | `getTestRun` | 查询测试运行 |
| `GET` | `/api/v1/projects/{project_id}/command-center` | `getCommandCenter` | 查询质量驾驶舱 |
| `GET` | `/api/v1/projects/{project_id}/live-map` | `getLiveMap` | 查询实时测试地图 |
| `GET` | `/api/v1/projects/{project_id}/risks` | `listRisks` | 查询 AI 风险列表 |
| `GET` | `/api/v1/projects/{project_id}/risks/{risk_id}` | `getRiskDetail` | 查询风险证据链详情 |
| `GET` | `/api/v1/projects/{project_id}/value-metrics` | `getValueMetrics` | 查询 ROI 价值指标 |
| `GET` | `/api/v1/projects/{project_id}/reports/executive` | `getExecutiveReport` | 查询领导成果战报 |
| `POST` | `/api/v1/projects/{project_id}/reports/generate` | `generateExecutiveReport` | 生成领导成果战报 |

## 前端集成顺序

1. `POST /api/v1/projects` 创建项目。
2. `POST /business-model/apply-template` 应用行业模板。
3. `PATCH /environment/config` 保存环境配置。
4. `POST /environment/preflight` 执行环境预检。
5. `POST /test-plan/generate` 生成 AI 测试计划。
6. `POST /test-runs` 启动测试运行。
7. `GET /command-center`、`GET /live-map`、`GET /risks`、`GET /reports/executive` 渲染 V1 页面。

## 错误响应规范

所有错误响应保持统一 envelope：`success=false`、`data=null`、`error.code`、`error.message`、`meta.generated_at`。错误文案面向客户解释，不暴露 Python traceback。

## 脱敏规范

合同示例只包含脱敏占位和状态字段，不包含原始凭证。Credential 类字段只允许进入本地运行时，不能进入报告、静态前端或交付包。
