# Phase105H 前端显示层总装

Phase105H 将 Phase105A-G 的前端显示层页面统一到一个完整入口：

- Phase105A：产品主界面
- Phase105B：质量驾驶舱
- Phase105C：客户资料导入
- Phase105D：环境诊断中心
- Phase105E：业务流程地图
- Phase105F：风险与证据链
- Phase105G：领导层报告 / ROI 价值中心

## 运行

```powershell
python -m ai_test_asset_center.phase105_frontend_experience_hub --scenario manufacturing --output-dir .\outputs\phase105_frontend_experience_hub
Start-Process .\outputs\phase105_frontend_experience_hub\index.html
```

## 只验收已有输出

```powershell
python -m ai_test_asset_center.phase105_frontend_experience_hub --validate-only --output-dir .\outputs\phase105_frontend_experience_hub
```

## 输出

- `index.html`
- `assets/qualibug_frontend_hub.css`
- `assets/qualibug_frontend_hub.js`
- `data/frontend_experience_hub_data.json`
- `pages/product_shell/index.html`
- `pages/dashboard/dashboard.html`
- `pages/customer_intake/customer_intake.html`
- `pages/environment_diagnosis/environment_diagnosis.html`
- `pages/business_flow_map/business_flow_map.html`
- `pages/risk_evidence/risk_evidence.html`
- `pages/report_roi/report_roi.html`
- `frontend_experience_hub_manifest.json`
- `frontend_experience_hub_acceptance_report.json`
- `frontend_experience_hub_acceptance_report.md`
- `phase105_frontend_experience_hub.zip`

## 验收重点

1. 总入口可打开。
2. 所有 Phase105A-G 页面可跳转。
3. 每个页面保留自己的独立 manifest 和数据文件。
4. 总装页包含体验旅程、页面入口和体验验收门禁。
5. 输出默认脱敏，不展示原始 token、cookie、password、session 或 client secret。
