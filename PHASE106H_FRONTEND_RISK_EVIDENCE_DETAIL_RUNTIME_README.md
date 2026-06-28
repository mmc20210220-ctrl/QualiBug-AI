# Phase106H 前端风险证据链真实详情页

本阶段在 Phase106G 实时执行事件流之后，补齐 `riskId / evidenceId / runId` 跳转后的真实证据详情页。

## 目标

- 风险证据详情真实读取
- 请求摘要 / 响应摘要展示
- 业务状态快照展示
- 复现步骤展示
- 修复建议展示
- 关闭条件展示
- 证据可信度展示
- 项目级证据上下文
- demo mode / real API mode / demo fallback
- 默认脱敏，不展示 token、cookie、session、password 原值

## 运行

```powershell
python -m ai_test_asset_center.phase106_frontend_risk_evidence_detail_runtime --scenario manufacturing --output-dir .\outputs\phase106_frontend_risk_evidence_detail_runtime
cd .\outputs\phase106_frontend_risk_evidence_detail_runtime\frontend_app
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173/risk-evidence-runtime
```

## 真实 API 模式

```text
VITE_QUALIBUG_DEMO_MODE=false
VITE_QUALIBUG_FALLBACK_TO_DEMO=true
VITE_QUALIBUG_API_BASE_URL=http://127.0.0.1:8790
VITE_QUALIBUG_SAFE_EXECUTION_MODE=read_only
```
