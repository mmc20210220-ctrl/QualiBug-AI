# Phase105B：质量驾驶舱 UI 强化

Phase105A 已完成前端产品壳和页面信息架构。Phase105B 聚焦最重要的首页：企业质量驾驶舱。

## 目标

让客户和领导在 30 秒内看懂：

- 当前是否建议上线
- 为什么不能上线或为什么可以灰度上线
- Top 风险是什么、影响哪些业务链路
- 客户环境是否可测、当前阻断在哪里
- AI 测试覆盖了哪些核心链路
- AI 测试创造了多少可解释的价值

## 生成内容

```text
Dashboard.html
assets/qualibug_dashboard_experience.css
assets/qualibug_dashboard_experience.js
data/dashboard_experience_data.json
README_DASHBOARD_EXPERIENCE.md
dashboard_experience_manifest.json
dashboard_experience_acceptance_report.json
dashboard_experience_acceptance_report.md
```

## 本地运行

```powershell
python -m ai_test_asset_center.phase105_dashboard_experience --scenario manufacturing --output-dir .\outputs\phase105_dashboard_experience
Start-Process .\outputs\phase105_dashboard_experience\dashboard.html
```

## 只验收已有输出

```powershell
python -m ai_test_asset_center.phase105_dashboard_experience --validate-only --output-dir .\outputs\phase105_dashboard_experience
```

## 验收点

- 必需文件完整
- Dashboard view-model 完整
- KPI、上线建议、Top 风险、业务覆盖、环境可测性、ROI 全部可渲染
- JavaScript renderer 覆盖核心区块
- 不输出 token、cookie、session、password、client_secret、traceback 原文
