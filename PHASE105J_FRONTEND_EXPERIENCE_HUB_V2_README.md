# Phase105J · 前端显示层总装 V2

Phase105J 在 Phase105H 统一前端体验 Hub 的基础上，接入 Phase105I 的 **AI 测试计划 + 实时测试执行页**，让前端显示层从“页面集合”升级为完整产品旅程。

## 新增能力

1. 新增总装 V2 模块 `phase105_frontend_experience_hub_v2.py`。
2. 总入口覆盖 8 个核心页面。
3. 接入 `pages/test_execution/test_execution.html`。
4. 客户旅程补齐 “AI 执行” 阶段。
5. 首页 KPI 显示 AI 测试执行页接入状态。
6. 页面入口卡片增加旅程阶段标签。
7. 验收门禁校验测试执行页存在、链接存在、manifest 存在。
8. zip 归档校验包含 test_execution 页面。
9. 继续保持 token / cookie / password / session / client secret / traceback 泄露扫描门禁。

## 生成命令

```powershell
python -m ai_test_asset_center.phase105_frontend_experience_hub_v2 --scenario manufacturing --output-dir .\outputs\phase105_frontend_experience_hub_v2
Start-Process .\outputs\phase105_frontend_experience_hub_v2\index.html
```

## 验收命令

```powershell
python -m ai_test_asset_center.phase105_frontend_experience_hub_v2 --validate-only --output-dir .\outputs\phase105_frontend_experience_hub_v2
```

## 输出文件

- `index.html`
- `assets/qualibug_frontend_hub_v2.css`
- `assets/qualibug_frontend_hub_v2.js`
- `data/frontend_experience_hub_v2_data.json`
- `pages/product_shell/index.html`
- `pages/dashboard/dashboard.html`
- `pages/customer_intake/customer_intake.html`
- `pages/environment_diagnosis/environment_diagnosis.html`
- `pages/business_flow_map/business_flow_map.html`
- `pages/test_execution/test_execution.html`
- `pages/risk_evidence/risk_evidence.html`
- `pages/report_roi/report_roi.html`
- `frontend_experience_hub_v2_manifest.json`
- `frontend_experience_hub_v2_acceptance_report.json`
- `frontend_experience_hub_v2_acceptance_report.md`
- `phase105_frontend_experience_hub_v2.zip`

## 完整前端旅程

```text
客户资料导入
→ 环境诊断
→ 业务流程地图
→ AI 测试计划
→ 实时测试执行
→ 风险证据链
→ 领导层报告 / ROI
```
