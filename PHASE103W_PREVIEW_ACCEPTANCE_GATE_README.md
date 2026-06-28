# Phase103W - 本地预览验收门禁

Phase103W 在 Phase103V 本地预览服务之上增加一个无浏览器、无第三方依赖的验收门禁。
它会自动生成预览站点，并验证静态页面、V1 只读 API、驾驶舱业务价值、环境适配解释性、实时地图、风险证据链、领导战报、ROI 指标和敏感信息脱敏。

## 一键验收全部演示场景

```powershell
python -m ai_test_asset_center.phase103_preview_acceptance --output-dir .\outputs\phase103_preview_acceptance
```

默认验收：

- manufacturing
- ecommerce
- saas

## 只验收单个场景

```powershell
python -m ai_test_asset_center.phase103_preview_acceptance --scenario manufacturing --output-dir .\outputs\phase103_preview_acceptance_manufacturing
```

## 输出文件

```text
acceptance_summary.json
acceptance_summary.md
manufacturing/acceptance_report.json
manufacturing/acceptance_report.md
ecommerce/acceptance_report.json
ecommerce/acceptance_report.md
saas/acceptance_report.json
saas/acceptance_report.md
```

## 验收项

- 静态页面与 CSS/JS 资产完整
- Preview manifest 可读取
- V1 只读 API 路由可用
- 质量驾驶舱具备领导层决策价值
- 环境适配诊断可解释
- 实时地图具备节点、链路和风险层
- 风险详情具备业务影响和脱敏证据链
- 领导层成果战报可汇报
- 静态页面与 API 不泄露 token/cookie/password/session/client_secret
- 预览服务只读且防目录穿越

## 安全说明

验收报告默认只记录脱敏后的页面/API 校验结果，不包含 token、cookie、password、session 原值或客户敏感数据。
