# Phase106G Frontend Execution Runtime

Phase106G adds project/run-scoped realtime execution and risk evidence feedback runtime to the generated Vite + React frontend app.

## New runtime route

`/execution-runtime`

## Capabilities

- 实时执行事件流
- 风险证据回流
- runId 执行状态
- 风险信号列表
- 证据快照列表
- 跳转证据链 handoff
- Phase104 API path contract
- demo mode / real API mode / demo fallback
- 默认脱敏，不展示原始 token、cookie、session、password 或 client secret

## Generate

```powershell
python -m ai_test_asset_center.phase106_frontend_execution_runtime --scenario manufacturing --output-dir .\outputs\phase106_frontend_execution_runtime
```

## Run frontend

```powershell
cd .\outputs\phase106_frontend_execution_runtimerontend_app
npm install
npm run dev
```

Open `http://127.0.0.1:5173/execution-runtime`.
