# Phase105A 前端产品壳

Phase105A 将后续重点从 Bug 引擎和交付包切换到前端显示层。它生成一个框架无关的企业级产品壳，用真实 Phase104 本地 API 演示数据驱动核心页面，帮助验证页面信息架构、导航、指标展示、风险表达、证据链详情、领导层报告与 ROI 价值中心。

## 生成

```powershell
python -m ai_test_asset_center.phase105_frontend_product_shell --output-dir .\outputs\phase105_frontend_product_shell
```

## 指定场景

```powershell
python -m ai_test_asset_center.phase105_frontend_product_shell --scenario manufacturing --api-base-url http://127.0.0.1:8790 --output-dir .\outputs\phase105_frontend_product_shell
```

## 只验收已有产品壳

```powershell
python -m ai_test_asset_center.phase105_frontend_product_shell --validate-only --output-dir .\outputs\phase105_frontend_product_shell
```

## 输出

- `index.html`
- `assets/qualibug_product_shell.css`
- `assets/qualibug_product_shell.js`
- `data/product_shell_data.json`
- `README_PRODUCT_SHELL.md`
- `product_shell_manifest.json`
- `product_shell_acceptance_report.json`
- `product_shell_acceptance_report.md`

## 页面范围

- 质量驾驶舱
- 客户资料导入
- 环境诊断中心
- 业务流程地图
- AI 测试计划
- 实时测试执行
- 风险与 Bug 列表
- 证据链详情
- 领导层报告
- ROI 价值中心
- 系统设置

## 设计原则

- 先让客户和领导看懂业务风险，再进入技术证据。
- 环境诊断页面必须解释阻断原因和客户下一步动作。
- 风险页面必须以业务语言展示，并保留证据链入口。
- 所有前端演示数据默认脱敏，不展示原始凭证、会话或客户敏感数据。
