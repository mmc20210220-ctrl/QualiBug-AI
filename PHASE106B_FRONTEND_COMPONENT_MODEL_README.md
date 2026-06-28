# Phase106B 前端组件模型真实化

Phase106B 在 Phase106A 的 Vite + React + TypeScript 前端工程脚手架基础上，补齐真实前端开发所需的组件模型、数据边界和 demo/real API 模式切换。

## 新增能力

- `demo mode` / `real API mode` 显式数据模式
- `QualiBugDataSource` 数据源边界
- `useQualiBugData` 页面加载 Hook
- `PageShell`、`DataModeBadge`、`KpiRail`、`FlowNodeCard`、`ProbeTable`、`RiskList`、`ActionQueue` 等组件
- `/component-model` 组件模型工作台路由
- 前端组件模型 contract test
- manifest / acceptance report / checksum / zip 交付物
- token / cookie / session / client_secret / traceback 泄露扫描门禁

## 生成

```powershell
python -m ai_test_asset_center.phase106_frontend_component_model --scenario manufacturing --output-dir .\outputs\phase106_frontend_component_model
```

## 启动前端

```powershell
cd .\outputs\phase106_frontend_component_model\frontend_app
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173/component-model
```

## 复验

```powershell
python -m ai_test_asset_center.phase106_frontend_component_model --validate-only --output-dir .\outputs\phase106_frontend_component_model
```

## 输出

```text
frontend_app/src/app/appConfig.ts
frontend_app/src/app/dataMode.ts
frontend_app/src/services/qualibugDataSource.ts
frontend_app/src/hooks/useQualiBugData.ts
frontend_app/src/components/PageShell.tsx
frontend_app/src/components/DataModeBadge.tsx
frontend_app/src/components/KpiRail.tsx
frontend_app/src/components/FlowNodeCard.tsx
frontend_app/src/components/ProbeTable.tsx
frontend_app/src/components/RiskList.tsx
frontend_app/src/components/ActionQueue.tsx
frontend_app/src/pages/ComponentModelWorkbenchPage.tsx
frontend_app/src/__tests__/component-model-contract.test.ts
frontend_app/src/styles/component-model.css
frontend_app/README_FRONTEND_COMPONENT_MODEL.md
frontend_component_model_manifest.json
frontend_component_model_acceptance_report.json
CHECKSUMS_PHASE106B.sha256
phase106_frontend_component_model.zip
```

## 下一步

Phase106C 应继续接真实 API Client 运行模式，把环境诊断、测试计划、风险证据和报告页逐步切到真实接口。
