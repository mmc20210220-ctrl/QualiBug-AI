# Phase106A：前端工程化真实应用脚手架

Phase105 已经完成静态展示层、交付包、本地预览服务、验收门禁、发布包和一键冒烟演示。Phase106A 开始进入真实前端工程化：生成一个 Vite + React + TypeScript 应用脚手架，用来承接 Phase104 API 合同和 Phase105 产品页面。

## 本阶段新增能力

1. 生成 `frontend_app/package.json`
2. 生成 Vite / React / TypeScript 配置
3. 生成 `.env.example`
4. 生成 typed QualiBug API Client
5. 生成真实前端路由清单
6. 生成可复用组件：Sidebar、Topbar、MetricCard、StatusPill、JourneyStepper、PageCard、EvidenceBadge
7. 生成页面组件：质量驾驶舱、客户资料导入、环境诊断、业务流程地图、AI 测试计划 / 实时测试执行、风险证据链、领导层报告 / ROI
8. 生成设计 token 和应用 CSS
9. 生成 demo data TypeScript 模块
10. 生成前端合同测试骨架
11. 生成 scaffold manifest
12. 生成 scaffold acceptance report
13. 生成 checksum ledger
14. 生成 zip 归档
15. 扫描 token / cookie / session / client_secret / traceback 泄露

## 使用方式

```powershell
python -m ai_test_asset_center.phase106_frontend_app_scaffold --scenario manufacturing --output-dir .\outputs\phase106_frontend_app_scaffold
```

进入生成的前端工程：

```powershell
cd .\outputs\phase106_frontend_app_scaffold\frontend_app
npm install
npm run dev
```

只复验已有脚手架：

```powershell
python -m ai_test_asset_center.phase106_frontend_app_scaffold --validate-only --output-dir .\outputs\phase106_frontend_app_scaffold
```

## 产物

```text
frontend_app/package.json
frontend_app/index.html
frontend_app/vite.config.ts
frontend_app/tsconfig.json
frontend_app/.env.example
frontend_app/src/main.tsx
frontend_app/src/App.tsx
frontend_app/src/routes.ts
frontend_app/src/api/qualibugClient.ts
frontend_app/src/components/*.tsx
frontend_app/src/pages/*.tsx
frontend_app/src/styles/*.css
frontend_app/src/__tests__/frontend-contract.test.ts
frontend_app/README_FRONTEND_APP.md
frontend_app_scaffold_manifest.json
frontend_app_scaffold_manifest.md
frontend_app_scaffold_acceptance_report.json
frontend_app_scaffold_acceptance_report.md
CHECKSUMS.sha256
phase106_frontend_app_scaffold.zip
```

## 下一阶段建议

Phase106B：把 Phase105 的静态页面数据结构正式映射成 React 组件模型，并补齐真实 API mock / demo mode 切换。
