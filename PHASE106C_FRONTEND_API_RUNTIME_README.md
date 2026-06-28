# Phase106C：真实 API Client 运行模式接入

Phase106C 继续推进前端工程化：在 Phase106B 组件模型基础上，补齐真实 API 运行边界。

## 新增能力

- 生成 `runtimeConfig.ts`，集中管理 API base URL、demo/real API mode、fallback、请求超时和安全执行模式。
- 生成 `runtimeApi.ts`，支持 `AbortController` 超时、API envelope 归一化、runtime health 和错误脱敏。
- 生成 `RuntimeApiAdapter`，将环境诊断、测试计划、测试执行、风险证据和领导报告切到 Phase104 API。
- 保留 `demo fallback`，客户现场 API 不可用时不影响演示。
- 新增 `/api-runtime` 工作台。
- 新增 API runtime contract test、manifest、acceptance report、checksum 和 zip。

## 生成

```powershell
python -m ai_test_asset_center.phase106_frontend_api_runtime --scenario manufacturing --output-dir .\outputs\phase106_frontend_api_runtime
```

## 启动前端

```powershell
cd .\outputs\phase106_frontend_api_runtime\frontend_app
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173/api-runtime
```

## 切真实 API

```text
VITE_QUALIBUG_DEMO_MODE=false
VITE_QUALIBUG_FALLBACK_TO_DEMO=true
VITE_QUALIBUG_API_BASE_URL=http://127.0.0.1:8790
VITE_QUALIBUG_SAFE_EXECUTION_MODE=read_only
```

## 复验

```powershell
python -m ai_test_asset_center.phase106_frontend_api_runtime --validate-only --output-dir .\outputs\phase106_frontend_api_runtime
```
