# Phase104A：企业质量指挥中心可写本地 HTTP API

Phase104A 在 Phase103S 的框架无关 API 门面层之上，增加一个标准库实现的本地 HTTP API 服务。它用于把 V1 页面、前端工程、本地演示和后续真实 Web 后端逐步接起来。

## 新增能力

- `GET /api/v1/health`
- `GET /api/v1/industry-templates`
- `GET /api/v1/projects`
- `POST /api/v1/projects`
- `GET /api/v1/projects/{project_id}`
- `GET /api/v1/projects/{project_id}/onboarding`
- `POST /api/v1/projects/{project_id}/business-model/apply-template`
- `GET/PATCH /api/v1/projects/{project_id}/business-model`
- `PATCH /api/v1/projects/{project_id}/environment/config`
- `POST /api/v1/projects/{project_id}/environment/preflight`
- `GET /api/v1/projects/{project_id}/environment/readiness`
- `POST /api/v1/projects/{project_id}/test-plan/generate`
- `GET /api/v1/projects/{project_id}/test-plan`
- `POST /api/v1/projects/{project_id}/test-runs`
- `GET /api/v1/projects/{project_id}/test-runs/{run_id}`
- `GET /api/v1/projects/{project_id}/command-center`
- `GET /api/v1/projects/{project_id}/live-map`
- `GET /api/v1/projects/{project_id}/risks`
- `GET /api/v1/projects/{project_id}/risks/{risk_id}`
- `GET /api/v1/projects/{project_id}/value-metrics`
- `POST /api/v1/projects/{project_id}/reports/generate`
- `GET /api/v1/projects/{project_id}/reports/executive`

## 本地启动

```powershell
python -m ai_test_asset_center.phase104_command_center_http_api --seed-scenario manufacturing --port 8790
```

然后访问：

```text
http://127.0.0.1:8790/api/v1/health
http://127.0.0.1:8790/api/v1/projects
```

## 前端联调价值

Phase103V 是只读预览服务，适合演示静态原型；Phase104A 则支持创建项目、应用行业模板、保存环境配置、执行预检、生成测试计划、启动测试运行和生成领导战报。

这意味着后续真实前端可以先直接对接本地 API，不需要等 FastAPI/Flask/企业部署版本完成。

## 安全约束

- 所有响应都会经过脱敏路径。
- 不返回 token、cookie、password、session、client_secret 原值。
- 错误响应为客户安全语言，不暴露 Python traceback。
- 支持 CORS OPTIONS 预检，便于本地前端工程联调。

## 测试

```powershell
python -m pytest -q tests/test_phase104a_command_center_http_api.py
```
