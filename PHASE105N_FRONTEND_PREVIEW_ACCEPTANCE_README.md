# Phase105N 前端预览服务验收门禁

Phase105N 用于验收 Phase105M 本地预览服务是否真的达到客户演示、交付复验和后续前端联调标准。

它不需要真正打开网络端口，而是直接调用 Phase105M 的纯路由层完成验收：

- `/`、`/pages/...` 等静态页面可访问。
- `/api/v1/frontend-preview/health`、`/manifest`、`/pages`、`/acceptance`、`/delivery`、`/handoff`、`/checksums` 可访问。
- 8 个核心页面完整：产品壳、质量驾驶舱、客户资料导入、环境诊断、业务流程地图、测试执行、风险证据链、领导报告 ROI。
- 关键页面包含业务语义文案。
- handoff 文档可读。
- CHECKSUMS 可读。
- zip、manifest、交付验收报告可读。
- POST 等写入方法被拒绝。
- 目录穿越被阻止。
- token / cookie / session / client_secret / traceback 无原始泄露。

## 一键构建并验收

```powershell
python -m ai_test_asset_center.phase105_frontend_preview_acceptance --build-first --bundle-dir .\outputs\phase105_frontend_delivery_bundle --output-dir .\outputs\phase105_frontend_preview_acceptance
```

## 只验收已有 Phase105L/Phase105M 输出

```powershell
python -m ai_test_asset_center.phase105_frontend_preview_acceptance --bundle-dir .\outputs\phase105_frontend_delivery_bundle --output-dir .\outputs\phase105_frontend_preview_acceptance
```

## 输出

- `frontend_preview_acceptance_report.json`
- `frontend_preview_acceptance_report.md`
- `frontend_preview_acceptance_manifest.json`

Phase105N 的定位是：把前端交付包预览服务从“能启动”推进到“可证明可演示”。
