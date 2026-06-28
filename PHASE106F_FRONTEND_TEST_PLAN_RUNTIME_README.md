# Phase106F Frontend Test Plan Runtime

Phase106F adds project-scoped AI test plan generation and read-only execution launch runtime to the generated Vite + React frontend app.

## New runtime route

`/test-plan-runtime`

## Capabilities

- AI 测试计划真实生成
- 可执行探针 / 阻断探针归一化
- 阻断探针原因展示
- 只读安全执行启动
- runId 回传
- 执行状态轮询
- 执行事件读取
- Phase104 API path contract
- demo mode / real API mode / demo fallback
- 默认脱敏，不展示原始 token、cookie、session、password 或 client secret

## Generate

```powershell
python -m ai_test_asset_center.phase106_frontend_test_plan_runtime --scenario manufacturing --output-dir .\outputs\phase106_frontend_test_plan_runtime
```

## Run frontend

```powershell
cd .\outputs\phase106_frontend_test_plan_runtimerontend_app
npm install
npm run dev
```

Open `http://127.0.0.1:5173/test-plan-runtime`.
