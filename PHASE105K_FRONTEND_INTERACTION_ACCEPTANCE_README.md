# Phase105K 前端显示层交互验收门禁

Phase105K 在 Phase105J 前端体验 Hub V2 之上增加“交互验收门禁”。它不是继续堆单页，而是把前端显示层从“能打开、能展示”推进到“可验收、可演示、可进入 CI”。

## 解决的问题

- 总入口是否能跳转到所有核心页面。
- 客户资料导入、环境诊断、业务流程地图、AI 测试计划、实时测试执行、风险证据链、领导层报告和 ROI 是否形成完整旅程。
- 页面是否有客户能理解的关键动作：复制、打开、进入、查看、下一步、重新、生成。
- 每个页面是否包含业务语义，而不是只有技术字段。
- 是否包含 Phase104 API 交接信息，便于后续真实前端接后端。
- 是否默认脱敏，避免 token、cookie、session、client_secret、traceback 泄露。
- zip 归档是否包含关键页面。

## 生成并验收

```powershell
python -m ai_test_asset_center.phase105_frontend_interaction_acceptance --build-first --hub-dir .\outputs\phase105_frontend_experience_hub_v2 --output-dir .\outputs\phase105_frontend_interaction_acceptance
```

## 只验收已有 Hub

```powershell
python -m ai_test_asset_center.phase105_frontend_interaction_acceptance --hub-dir .\outputs\phase105_frontend_experience_hub_v2 --output-dir .\outputs\phase105_frontend_interaction_acceptance
```

## 输出文件

- `frontend_interaction_acceptance_report.json`
- `frontend_interaction_acceptance_report.md`
- `frontend_interaction_acceptance_manifest.json`

## 验收标准

- 必须有 Phase105J Hub V2 manifest。
- 必须有总入口 `index.html`。
- 8 个核心页面必须存在。
- 总入口必须包含所有页面跳转。
- 页面数据状态必须 ready。
- 客户旅程必须覆盖资料、环境、业务、执行、风险、报告和 ROI。
- 页面必须包含客户动作和下一步动作。
- 必须检测到前端交互脚本。
- 必须检测到 Phase104 API 交接信息。
- 不允许出现原始 token、cookie、password、session、client_secret 或 traceback。
