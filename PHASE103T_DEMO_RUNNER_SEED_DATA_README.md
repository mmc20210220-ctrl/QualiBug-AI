# Phase103T Demo Runner + Seed Data

本补丁在 Phase103R/S 的基础上增加本地演示运行器，用于一条命令生成企业质量指挥中心 V1 的页面级种子数据。

## 新增能力

- 制造 ERP、电商、SaaS 三套演示场景。
- 自动创建项目、应用行业模板、生成业务模型。
- 自动生成客户环境适配报告、AI 测试计划、测试运行、风险卡片、证据链。
- 自动生成质量驾驶舱、实时地图、ROI 指标和领导层成果战报。
- 支持导出前端可直接消费的 JSON 文件和 Markdown 摘要。
- 所有导出默认走统一脱敏路径，不暴露 token、cookie、password、session 原值。

## 本地命令

```powershell
python -m ai_test_asset_center.phase103_demo_runner --scenario manufacturing --output-dir .\outputs\phase103_demo_manufacturing
python -m ai_test_asset_center.phase103_demo_runner --scenario ecommerce --output-dir .\outputs\phase103_demo_ecommerce
python -m ai_test_asset_center.phase103_demo_runner --scenario saas --output-dir .\outputs\phase103_demo_saas
```

## 主要产物

- `project.json`
- `business_model.json`
- `environment_readiness.json`
- `test_plan.json`
- `command_center.json`
- `live_map.json`
- `risks.json`
- `risk_details.json`
- `value_metrics.json`
- `executive_report.json`
- `frontend_pages.json`
- `README_demo_summary.md`
- `manifest.json`

## 验证

新增测试覆盖：

- 场景列表与安全脱敏。
- 制造 ERP 端到端演示数据生成。
- 电商支付和权限风险演示。
- SaaS 跨租户风险演示摘要。
- 前端 JSON / Markdown 导出。
- CLI 本地生成 smoke 测试。
